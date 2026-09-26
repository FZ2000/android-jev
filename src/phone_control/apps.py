"""Finding and launching installed apps by the name a person would use.

Nobody says "com.google.android.youtube"; they say "YouTube". This module
bridges that gap, and always checks the phone's real package list before
trusting any name.
"""

from __future__ import annotations

import time

from .adb import AndroidPhone, quote_for_device_shell
from .errors import PhoneCommandFailed

# Both launch commands print their complaint and still exit zero, so the output
# is the only evidence that nothing started.
START_REFUSAL_MARKERS = (
    "Error:",
    "Error type",
    "does not exist",
    "Permission Denial",
    "unable to resolve",
    "Exception",
)

MONKEY_REFUSAL_MARKERS = ("aborted", "No activities found")


def _was_refused(output: str, markers: tuple[str, ...]) -> bool:
    """Whether a launch command said, in its output, that nothing started."""
    return any(marker in output for marker in markers)


PACKAGE_CACHE_SECONDS = 60.0

# Names people actually use, mapped to the package that ships them. Every entry
# is still verified against the installed package list before use.
# What each app is called, per package, for the list the decider chooses from.
# The name matters more than it looks: offering ``vending`` and ``deskclock`` and
# ``gm`` asks the decider to choose between three words no person uses, and this
# project's own matching failed on exactly that, because "open the clock app" had
# to be reconciled with a package called deskclock.
SPOKEN_APP_NAMES: dict[str, str] = {
    "com.android.chrome": "Chrome",
    "com.android.settings": "Settings",
    "com.android.vending": "Play Store",
    "com.fitbit.FitbitMobile": "Fitbit",
    "com.google.android.GoogleCamera": "Camera",
    "com.google.android.apps.adm": "Device Manager",
    "com.google.android.apps.chromecast.app": "Google Home",
    "com.google.android.apps.docs": "Drive",
    "com.google.android.apps.docs.editors.docs": "Google Docs",
    "com.google.android.apps.magazines": "Google News",
    "com.google.android.apps.maps": "Maps",
    "com.google.android.apps.messaging": "Messages",
    "com.google.android.apps.nbu.files": "Files",
    "com.google.android.apps.photos": "Photos",
    "com.google.android.apps.recorder": "Recorder",
    "com.google.android.apps.safetyhub": "Safety",
    "com.google.android.apps.subscriptions.red": "Google One",
    "com.google.android.apps.tachyon": "Google Meet",
    "com.google.android.apps.tips": "Tips",
    "com.google.android.apps.translate": "Translate",
    "com.google.android.apps.walletnfcrel": "Wallet",
    "com.google.android.apps.wear.companion": "Watch",
    "com.google.android.apps.wearables.maestro.companion": "Pixel Buds",
    "com.google.android.apps.weather": "Weather",
    "com.google.android.apps.youtube.music": "YouTube Music",
    "com.google.android.calculator": "Calculator",
    "com.google.android.calendar": "Calendar",
    "com.google.android.contacts": "Contacts",
    "com.google.android.deskclock": "Clock",
    "com.google.android.dialer": "Phone",
    "com.google.android.gm": "Gmail",
    "com.google.android.googlequicksearchbox": "Google",
    "com.google.android.keep": "Keep",
    "com.google.android.videos": "Google TV",
    "com.google.android.youtube": "YouTube",
}


def spoken_name(package: str) -> str:
    """What a person calls this app, falling back to its package's last word."""
    return SPOKEN_APP_NAMES.get(package) or humanize_package(package)


def common_app_aliases() -> dict[str, str]:
    """Every name an app answers to, as one lookup of name -> package.

    The per-package names come first and win, being the only ones certainly
    right. The general synonyms fill in behind them, and two of those were wrong
    for this phone in a way worth recording: the old table sent "files" to the
    documents provider rather than to the Files app, and "docs" to Drive rather
    than to Google Docs. An offered option that resolves to a different app is an
    option that cannot be carried out.
    """
    aliases: dict[str, str] = {}
    for package, name in SPOKEN_APP_NAMES.items():
        aliases.setdefault(name.casefold(), package)
    for name, package in COMMON_APP_ALIASES.items():
        aliases.setdefault(name.casefold(), package)
    return aliases


COMMON_APP_ALIASES: dict[str, str] = {
    "youtube": "com.google.android.youtube",
    "youtube music": "com.google.android.apps.youtube.music",
    "chrome": "com.android.chrome",
    "browser": "com.android.chrome",
    "gmail": "com.google.android.gm",
    "mail": "com.google.android.gm",
    "maps": "com.google.android.apps.maps",
    "google maps": "com.google.android.apps.maps",
    "camera": "com.google.android.GoogleCamera",
    "photos": "com.google.android.apps.photos",
    "settings": "com.android.settings",
    "messages": "com.google.android.apps.messaging",
    "messaging": "com.google.android.apps.messaging",
    "sms": "com.google.android.apps.messaging",
    "phone": "com.google.android.dialer",
    "dialer": "com.google.android.dialer",
    "clock": "com.google.android.deskclock",
    "alarm": "com.google.android.deskclock",
    "calendar": "com.google.android.calendar",
    "calculator": "com.google.android.calculator",
    "contacts": "com.google.android.contacts",
    "files": "com.google.android.documentsui",
    "play store": "com.android.vending",
    "play": "com.android.vending",
    "store": "com.android.vending",
    "drive": "com.google.android.apps.docs",
    "keep": "com.google.android.keep",
    "search": "com.google.android.googlequicksearchbox",
    "spotify": "com.spotify.music",
    "whatsapp": "com.whatsapp",
    "signal": "org.thoughtcrime.securesms",
    "telegram": "org.telegram.messenger",
    "instagram": "com.instagram.android",
    "facebook": "com.facebook.katana",
    "x": "com.twitter.android",
    "twitter": "com.twitter.android",
    "reddit": "com.reddit.frontpage",
    "slack": "com.Slack",
    "discord": "com.discord",
    "zoom": "us.zoom.videomeetings",
    "netflix": "com.netflix.mediaclient",
    "amazon": "com.amazon.mShop.android.shopping",
}

# Package segments that carry no distinguishing information.
_GENERIC_PACKAGE_SEGMENTS = frozenset(
    {
        "com",
        "org",
        "net",
        "io",
        "android",
        "google",
        "apps",
        "app",
        "mobile",
        "mobi",
        "inc",
        "labs",
    }
)

_EXACT_PACKAGE_SCORE = 120
_EXACT_TAIL_SCORE = 100
_EXACT_WORDS_SCORE = 90
_PREFIX_TAIL_SCORE = 70
_SUBSTRING_SCORE = 40

LAUNCHER_COMPONENT_TIMEOUT_SECONDS = 20.0


def humanize_package(package: str) -> str:
    """A short readable name derived from a package id."""
    segments = [segment for segment in package.split(".") if segment]
    meaningful = [
        segment
        for segment in segments
        if segment.casefold() not in _GENERIC_PACKAGE_SEGMENTS
    ]
    if not meaningful:
        return package
    return meaningful[-1].replace("_", " ").replace("-", " ").strip()


def _package_match_score(name: str, package: str) -> int:
    """How well a spoken name identifies a package; 0 means no match."""
    normalized = name.casefold().strip()
    if not normalized:
        return 0
    folded_package = package.casefold()
    if normalized == folded_package:
        return _EXACT_PACKAGE_SCORE
    segments = [segment for segment in folded_package.split(".") if segment]
    if not segments:
        return 0
    if normalized == segments[-1]:
        return _EXACT_TAIL_SCORE
    if normalized == " ".join(segments):
        return _EXACT_WORDS_SCORE
    if segments[-1].startswith(normalized):
        return _PREFIX_TAIL_SCORE
    if normalized.replace(" ", "") in folded_package.replace(".", ""):
        return _SUBSTRING_SCORE
    return 0


class InstalledApps:
    """The packages installed on a phone, cached for a short while."""

    def __init__(
        self, phone: AndroidPhone, cache_seconds: float = PACKAGE_CACHE_SECONDS
    ) -> None:
        self.phone = phone
        self.cache_seconds = cache_seconds
        self._packages: list[str] | None = None
        self._launchable: list[str] | None = None
        self._read_at = 0.0

    def packages(self) -> list[str]:
        now = time.monotonic()
        if self._packages is not None and now - self._read_at < self.cache_seconds:
            return self._packages
        return self.refresh()

    def refresh(self) -> list[str]:
        output = self.phone.shell("pm list packages -e")
        packages = sorted(
            line.strip()[len("package:") :].strip()
            for line in output.splitlines()
            if line.strip().startswith("package:")
        )
        self._packages = packages
        self._read_at = time.monotonic()
        return packages

    def resolve(self, name: str) -> str:
        """The package a spoken app name refers to."""
        wanted = name.strip()
        if not wanted:
            raise ValueError("An app name is required.")

        installed = self.packages()
        installed_lookup = {package.casefold(): package for package in installed}

        exact = installed_lookup.get(wanted.casefold())
        if exact:
            return exact

        alias = common_app_aliases().get(wanted.casefold())
        if alias:
            known = installed_lookup.get(alias.casefold())
            if known:
                return known

        scored = [
            (_package_match_score(wanted, package), package) for package in installed
        ]
        candidates = sorted(
            (pair for pair in scored if pair[0] > 0),
            key=lambda pair: (-pair[0], len(pair[1]), pair[1]),
        )
        if candidates:
            return candidates[0][1]

        from .errors import NoMatchingControl

        known_names = sorted(
            {humanize_package(package) for package in installed} - {""}
        )
        raise NoMatchingControl(
            f"No installed app matches '{name}'.",
            fix=(
                "Call list_apps to see what is installed, or pass an exact "
                "package id. Some examples on this phone: "
                + ", ".join(known_names[:25])
            ),
        )

    def names(self) -> list[str]:
        """The human names of installed apps, longest first.

        Longest first so a goal naming "play store" is matched by that name
        rather than by the shorter "store" that is also in the list.
        """
        found = {humanize_package(package) for package in self.packages()}
        return sorted((name for name in found if name), key=len, reverse=True)

    def launchable(self) -> list[str]:
        """The packages with a launcher activity, cached like the package list.

        Read from the launcher rather than from the installed list, because the
        two are different sets and the difference has cost us runs. An installed
        package need not be openable at all, and the launcher's set is thirty-five
        on this phone rather than several hundred - a size a choice question
        handles well. Settings is in it even though it has no icon on the
        launcher's first page, which is what makes "turn off Bluetooth" reachable
        from the home screen at all.
        """
        now = time.monotonic()
        if self._launchable is not None and now - self._read_at < self.cache_seconds:
            return self._launchable

        output = self.phone.shell(
            "cmd package query-activities --brief "
            "-a android.intent.action.MAIN "
            "-c android.intent.category.LAUNCHER"
        )
        self._launchable = sorted(
            {
                line.strip().split("/")[0]
                for line in output.splitlines()
                if "/" in line.strip() and not line.startswith("priority")
            }
        )
        return self._launchable

    def launchable_names(self) -> list[str]:
        """What those apps are called in words, which is what the decider sees."""
        return sorted({spoken_name(package) for package in self.launchable()})

    def as_listing(self) -> str:
        """Every package, under the name a person would use for it.

        The spoken names matter here as much as in the options: this listing is
        how an agent recovers from an app it could not find, and "vending" and
        "deskclock" are not answers to "which app is the clock".
        """
        packages = self.packages()
        lines = [f"{spoken_name(package)}  ->  {package}" for package in packages]
        return f"{len(packages)} installed packages:\n" + "\n".join(lines)


def resolve_launcher_component(phone: AndroidPhone, package: str) -> str | None:
    """The launchable activity for a package, or None when it has none."""
    try:
        output = phone.shell(
            "cmd package resolve-activity --brief "
            "-a android.intent.action.MAIN "
            "-c android.intent.category.LAUNCHER " + package,
            timeout=LAUNCHER_COMPONENT_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if "/" in candidate and not candidate.casefold().startswith("priority"):
            return candidate
    return None


def launch_app(phone: AndroidPhone, apps: InstalledApps, name: str) -> str:
    """Bring an app to the foreground, returning the package that was launched."""
    package = apps.resolve(name)
    component = resolve_launcher_component(phone, package)
    if component:
        # Quoted, because a component is a Java class name and inner classes
        # contain a dollar sign. Unquoted, the device shell reads $HomeActivity as
        # a variable and expands it to nothing, so am is asked to start an
        # activity that does not exist - observed on YouTube, whose launcher
        # activity is ...Shell$HomeActivity.
        output = phone.run(
            ["shell", "am", "start", "-W", "-n", quote_for_device_shell(component)]
        )
        if _was_refused(output, START_REFUSAL_MARKERS):
            raise PhoneCommandFailed(
                f"am start -n {component}",
                output.strip(),
                fix=(
                    "The app is installed but this entry point did not launch. "
                    "Open it once from the phone's launcher, or pass an exact "
                    "package id."
                ),
            )
    else:
        output = phone.run(
            [
                "shell",
                "monkey",
                "-p",
                package,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ]
        )
        if _was_refused(output, MONKEY_REFUSAL_MARKERS):
            raise PhoneCommandFailed(
                f"monkey -p {package}",
                output.strip(),
                fix="The app has no launcher entry point that could be started.",
            )
    return package


def open_link(phone: AndroidPhone, url: str) -> None:
    """Open a URL or deep link with whatever app claims it."""
    phone.run(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            quote_for_device_shell(url),
        ]
    )
