"""Repository-owned verification and native-distribution sessions."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import platform
import sys
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType

import nox

from openai_responses_proxy import product_identity

ROOT = Path(__file__).parent.resolve()


def _load_repository_module(name: str, relative: str) -> ModuleType:
    """Load repository orchestration without an ambient import search path."""
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / relative))
    spec = importlib.machinery.ModuleSpec(loader.name, loader, origin=loader.path)
    spec.has_location = True
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


python_matrix = _load_repository_module("proxy_python_matrix", "tools/quality/python_matrix.py")
python_quality = _load_repository_module("proxy_python_quality", "tools/quality/python.py")
verification = _load_repository_module("proxy_native_verification", "tools/release/verification.py")
PYTHONS = tuple((ROOT / ".config/ci/python-versions").read_text(encoding="utf-8").splitlines())
MIN_PYTHON, *_, MAX_PYTHON = PYTHONS
RELEASE_PYTHON = (ROOT / ".config/mise/python-release").read_text(encoding="utf-8").strip()
PERFORMANCE_POLICY = ROOT / ".config/checks/performance/policy.toml"
PYTEST_COMMAND = (
    "python",
    "-m",
    "pytest",
    "-q",
    "--rootdir",
    str(ROOT),
    "-c",
    str(python_quality.PYTEST_CONFIG),
)

nox.options.default_venv_backend = "uv"
nox.options.error_on_missing_interpreters = True
nox.options.reuse_existing_virtualenvs = False


@nox.session(python=MAX_PYTHON)
def quick(session: nox.Session) -> None:
    """Run the cheapest deterministic contract and source checks."""
    _prepare_product_session(session)
    python_quality.static(session, environment=_environment(), minimum_python=MIN_PYTHON)
    session.run(
        *PYTEST_COMMAND,
        "tests/quality/test_policy.py",
        "tests/quality/repository",
        "tests/quality/orchestration/test_governance.py",
        env=_environment(),
    )


@nox.session(python=PYTHONS)
def tests(session: nox.Session) -> None:
    """Compile and run the complete behavior inventory on one Python."""
    work = _prepare_product_session(session)
    environment: dict[str, str | None] = {
        **_environment(),
        product_identity.environment_name("EXECUTABLE"): str(_installed_executable(session)),
    }
    session.run(
        "python",
        "-m",
        "compileall",
        "-q",
        *python_quality.ROOTS,
        env={**environment, "PYTHONPYCACHEPREFIX": str(work / "pycache")},
    )
    python_quality.behavior(session, environment=environment)


@nox.session(python=MIN_PYTHON)
def quality(session: nox.Session) -> None:
    """Run static analysis and branch-aware coverage at the compatibility floor."""
    _prepare_product_session(session)
    environment: dict[str, str | None] = {
        **_environment(),
        product_identity.environment_name("EXECUTABLE"): str(_installed_executable(session)),
    }
    python_quality.static(session, environment=_environment(), minimum_python=MIN_PYTHON)
    python_quality.behavior(session, environment=environment, require_coverage=True)
    if os.environ.get("ETHOS_NATIVE_OUTPUT_DIR"):
        session.run(
            *PYTEST_COMMAND,
            "tests/quality/orchestration/test_observation.py",
            "-k",
            "native_behavior_reports_share_one_actual_pytest_attempt "
            "or native_static_report_observes_one_original_ruff_launch",
            env={**environment, "ETHOS_NATIVE_OUTPUT_DIR": None},
        )


@nox.session(python=RELEASE_PYTHON)
def performance(session: nox.Session) -> None:
    """Measure deterministic product overhead and emit machine-readable evidence."""
    _prepare_product_session(session)
    output = Path(session.posargs[0] if session.posargs else session.create_tmp()).resolve()
    output.mkdir(parents=True, exist_ok=True)
    benchmark_log = output / "benchmark.log"
    environment = {
        **_environment(),
        product_identity.environment_name("HOME"): str(output / "payload"),
        product_identity.environment_name("STATE_HOME"): str(output / "state"),
    }
    execution = tomllib.loads(PERFORMANCE_POLICY.read_text(encoding="utf-8"))["execution"]
    common = [
        "--quiet",
        "--processes",
        str(execution["processes"]),
        "--values",
        str(execution["values"]),
        "--warmups",
        str(execution["warmups"]),
        "--min-time",
        str(execution["minimum_time_seconds"]),
        "--output",
    ]
    with benchmark_log.open("w", encoding="utf-8") as log:
        session.run(
            "python",
            "-m",
            "tools.performance.benchmark",
            *common,
            str(output / "latency.json"),
            env=environment,
            stderr=log,
        )
        session.run(
            "python",
            "-m",
            "tools.performance.memory",
            "--track-memory",
            *common,
            str(output / "memory.json"),
            env=environment,
            stderr=log,
        )
    session.run(
        "python",
        "-m",
        "tools.performance.verify",
        "--policy",
        str(PERFORMANCE_POLICY),
        "--latency",
        str(output / "latency.json"),
        "--memory",
        str(output / "memory.json"),
        env=environment,
    )


@nox.session(python=False)
def governance(session: nox.Session) -> None:
    """Run the repository governance graph with the locked external toolchain."""
    session.run(
        sys.executable,
        "-m",
        "tools.quality.governance",
        *session.posargs,
        external=True,
        env=_environment(),
    )


@nox.session(python=False)
def fix(session: nox.Session) -> None:
    """Apply the repository formatters to every input that check verifies."""
    session.run(
        "ruff",
        "format",
        "--config",
        str(python_quality.RUFF_CONFIG),
        "--no-cache",
        ".",
        external=True,
        env=_environment(),
    )
    session.run(
        sys.executable,
        "-m",
        "tools.quality.governance",
        "--fix",
        external=True,
        env=_environment(),
    )


@nox.session(python=False)
def full(session: nox.Session) -> None:
    """Run governance, strict quality, and every remaining supported interpreter."""
    session.notify("governance")
    session.notify("quality")
    for python in PYTHONS[1:]:
        session.notify(f"tests-{python}")


@nox.session(python=RELEASE_PYTHON)
def release_asset(session: nox.Session) -> None:
    """Build and accept a native asset without requiring a host service manager."""
    _verify_native_candidate(session)


@nox.session(python=RELEASE_PYTHON)
def release(session: nox.Session) -> None:
    """Build and accept this platform's asset and native service lifecycle."""
    _verify_native_candidate(session, "tests/release/lifecycle")


@nox.session(python=False)
def native_lifecycle(session: nox.Session) -> None:
    """Accept an explicit built executable with surviving native teardown custody."""
    variable = product_identity.environment_name("NATIVE_EXECUTABLE")
    executable = _required_file(variable, "native executable")
    verification.accept(
        session,
        (sys.executable, *PYTEST_COMMAND[1:], "tests/release/lifecycle", *session.posargs),
        work=ROOT / "build" / "native-verification",
        environment={**_environment(), variable: str(executable)},
    )


def _verify_native_candidate(session: nox.Session, *lifecycle_cases: str) -> None:
    """Accept shared artifact behavior and selected lifecycle cases before packaging."""
    arguments = session.posargs[1:]
    if arguments and (len(arguments) != 1 or not arguments[0].startswith("--basetemp=")):
        session.error("native builders accept only an output directory and --basetemp=<path>")
    work, bundle, executable = _build_native_candidate(session)
    environment = {
        **_environment(),
        product_identity.environment_name("EXECUTABLE"): str(executable),
        product_identity.environment_name("NATIVE_EXECUTABLE"): str(executable),
    }
    verification.accept(
        session,
        (
            *PYTEST_COMMAND,
            "tests/cli/test_interface.py",
            *lifecycle_cases,
            "tests/serving/handoff/subprocess",
            "tests/relay/native/test_recovery.py",
            *arguments,
        ),
        work=ROOT / "build" / "native-verification",
        environment=environment,
    )
    _package_release_asset(session, bundle, work)


def _build_native_candidate(session: nox.Session) -> tuple[Path, Path, Path]:
    """Build one source-independent native candidate from the locked repository."""
    _install_tools(session, "quality", "release")
    _assert_release_runtime(session)
    work = Path(session.create_tmp()).resolve()
    wheel = _build_wheel(session, work)
    _install_wheel(session, wheel)
    session.run(
        "python",
        "-m",
        "tools.release.artifact",
        "normalize",
        "--packages",
        str(_session_packages(session)),
        env=_environment(),
    )
    _assert_installed_product(session, work)
    bundle, executable = _build_executable(session, work)
    return work, bundle, executable


@nox.session(python=RELEASE_PYTHON)
def release_compatibility(session: nox.Session) -> None:
    """Upgrade one verified published predecessor to this native candidate."""
    previous_asset = _required_file(
        product_identity.environment_name("PREVIOUS_RELEASE_ASSET"),
        "published predecessor asset",
    )
    previous_trust = _required_file(
        product_identity.environment_name("PREVIOUS_RELEASE_TRUST_ANCHOR"),
        "published predecessor trust anchor",
    )
    _work, bundle, executable = _build_native_candidate(session)
    verification.accept(
        session,
        (
            *PYTEST_COMMAND,
            "tests/release/compatibility",
        ),
        work=ROOT / "build" / "native-verification",
        environment={
            **_environment(),
            product_identity.environment_name("NATIVE_EXECUTABLE"): str(executable),
            product_identity.environment_name("NATIVE_BUNDLE"): str(bundle),
            product_identity.environment_name("PREVIOUS_RELEASE_ASSET"): str(previous_asset),
            product_identity.environment_name("PREVIOUS_RELEASE_TRUST_ANCHOR"): str(previous_trust),
        },
    )


@nox.session(python=RELEASE_PYTHON)
def published_release_compatibility(session: nox.Session) -> None:
    """Exercise one downloaded release against its published predecessor."""
    current_asset = _required_file(
        product_identity.environment_name("CURRENT_RELEASE_ASSET"),
        "published current release asset",
    )
    previous_asset = _required_file(
        product_identity.environment_name("PREVIOUS_RELEASE_ASSET"),
        "published predecessor asset",
    )
    trust = _required_file(
        product_identity.environment_name("CURRENT_RELEASE_TRUST_ANCHOR"),
        "published release trust anchor",
    )
    previous_trust = _required_file(
        product_identity.environment_name("PREVIOUS_RELEASE_TRUST_ANCHOR"),
        "published predecessor trust anchor",
    )
    _assert_release_runtime(session)
    work = _prepare_product_session(session, "quality")
    bundle, executable = verification.materialize_published_bundle(
        session,
        current_asset,
        trust,
        work,
        version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
    )
    verification.accept(
        session,
        (
            *PYTEST_COMMAND,
            "tests/cli/test_interface.py",
            "tests/serving/handoff/subprocess",
            "tests/relay/native/test_recovery.py",
            "tests/release/lifecycle",
            "tests/release/compatibility",
            *session.posargs,
        ),
        work=ROOT / "build" / "native-verification",
        environment={
            **_environment(),
            product_identity.environment_name("EXECUTABLE"): str(executable),
            product_identity.environment_name("NATIVE_EXECUTABLE"): str(executable),
            product_identity.environment_name("NATIVE_BUNDLE"): str(bundle),
            product_identity.environment_name("PREVIOUS_RELEASE_ASSET"): str(previous_asset),
            product_identity.environment_name("PREVIOUS_RELEASE_TRUST_ANCHOR"): str(previous_trust),
        },
    )


def _required_file(variable: str, label: str) -> Path:
    """Return one explicit regular file input without guessing a host path."""
    value = os.environ.get(variable, "")
    path = Path(value).expanduser() if value else Path()
    if not value or not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} is unavailable; set {variable}")
    return path.resolve(strict=True)


def _install_tools(session: nox.Session, *groups: str) -> None:
    """Install the repository-locked verification tool set into this session."""
    requirements = Path(session.create_tmp()) / "requirements.txt"
    command = [
        "uv",
        "export",
        "--locked",
        "--no-emit-project",
        "--format",
        "requirements.txt",
        "--output-file",
        str(requirements),
        "--project",
        str(ROOT),
    ]
    for group in groups or ("quality",):
        command.extend(("--group", group))
    environment = {**_environment(), "UV_NO_PROGRESS": "1"}
    session.run_install(
        *command,
        silent=True,
        env=environment,
        external=True,
    )
    session.install(
        "--requirements",
        str(requirements),
        "--strict",
        env=environment,
    )
    python_quality.install_native_helpers(session)


def _prepare_product_session(session: nox.Session, *groups: str) -> Path:
    """Supply locked tools, install one built wheel, and prove its exact source bytes."""
    _install_tools(session, *groups)
    work = Path(session.create_tmp()).resolve()
    wheel = _build_wheel(session, work)
    _install_wheel(session, wheel)
    _assert_installed_product(session, work)
    return work


def _build_wheel(session: nox.Session, work: Path) -> Path:
    """Build the exact wheel exercised by behavior and quality sessions."""
    wheelhouse = work / "wheelhouse"
    wheelhouse.mkdir()
    with TemporaryDirectory(prefix="wheel-build-cache-", dir=ROOT.parent) as cache:
        if Path(cache).resolve().is_relative_to(ROOT):
            raise RuntimeError("wheel build cache must be outside the source checkout")
        session.run_install(
            "uv",
            "build",
            "--wheel",
            "--offline",
            "--no-build-isolation",
            "--python",
            "python",
            "--cache-dir",
            cache,
            "--out-dir",
            str(wheelhouse),
            str(ROOT),
            external=True,
        )
    wheels = tuple(wheelhouse.glob("*.whl"))
    if len(wheels) != 1:
        session.error(f"expected one wheel, found {len(wheels)}")
    return wheels[0]


def _install_wheel(session: nox.Session, wheel: Path) -> None:
    """Install only the built product artifact, never the source checkout."""
    session.install(
        "--strict",
        str(wheel),
        env={"PYTHONNOUSERSITE": "1", "UV_NO_PROGRESS": "1"},
    )


def _session_packages(session: nox.Session) -> Path:
    """Return this session interpreter's installed package directory."""
    packages = session.run(
        "python",
        "-c",
        "import sysconfig; print(sysconfig.get_path('purelib'))",
        silent=True,
        env=_environment(),
    )
    if not isinstance(packages, str):
        session.error("release session package directory is unavailable")
    return Path(packages.strip()).resolve(strict=True)


def _assert_installed_product(session: nox.Session, work: Path) -> None:
    """Prove installed imports and every packaged source byte match this checkout."""
    manifest = work / "installed-source.json"
    manifest.write_text(json.dumps(python_quality.source_manifest(ROOT)), encoding="utf-8")
    probe = (
        "from pathlib import Path; "
        "import hashlib,json,sys,sysconfig; "
        "import openai_responses_proxy as package; "
        "from openai_responses_proxy.providers import registry; "
        "root = Path(package.__file__).resolve(); "
        "manifest = registry.default_manifest_path().resolve(); "
        "installed = Path(sysconfig.get_path('purelib')).resolve(); "
        "assert root.is_relative_to(installed); "
        "assert manifest.is_file() and manifest.is_relative_to(root.parent); "
        "files = tuple(p for p in root.parent.rglob('*') if '__pycache__' not in p.parts); "
        "assert not any(p.is_symlink() for p in files); "
        "observed = {p.relative_to(root.parent).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() "
        "for p in files if p.is_file()}; "
        "assert observed == json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))"
    )
    with session.chdir(work):
        session.run("python", "-I", "-c", probe, str(manifest), env=_environment())


def _installed_executable(session: nox.Session) -> Path:
    """Return the console executable installed from this session's built wheel."""
    name = product_identity.executable_name(windows=os.name == "nt")
    executable = Path(session.bin) / name
    if not executable.is_file():
        session.error(f"installed console executable was not produced: {executable}")
    return executable


def _build_executable(session: nox.Session, work: Path) -> tuple[Path, Path]:
    """Build one native directory bundle with only release-owned data."""
    name = product_identity.executable_name(windows=os.name == "nt")
    dist = work / "dist"
    command = (
        "pyinstaller",
        "--clean",
        "--noconfirm",
        "--log-level",
        "ERROR",
        "--onedir",
        "--name",
        product_identity.PRODUCT_SLUG,
        "--distpath",
        str(dist),
        "--workpath",
        str(work / "build"),
        "--specpath",
        str(work),
        "--additional-hooks-dir",
        str(ROOT / "tools/release/hooks"),
        "--add-data",
        f"{ROOT / 'VERSION'}{os.pathsep}.",
        "--add-data",
        (
            f"{ROOT / 'src/openai_responses_proxy/providers/manifest.toml'}"
            f"{os.pathsep}openai_responses_proxy/providers"
        ),
        "--collect-submodules",
        "openai_responses_proxy.providers.policies",
        str(ROOT / "src/openai_responses_proxy/cli/__main__.py"),
    )
    session.run(*command, env=_environment())
    bundle = dist / product_identity.PRODUCT_SLUG
    executable = bundle / name
    if not executable.is_file():
        session.error(f"native executable was not produced: {executable}")
    return bundle, executable


def _assert_release_runtime(session: nox.Session) -> None:
    """Reject native builds outside the repository-declared runtime."""
    try:
        release_version, _ = python_matrix.release_runtime(
            release=ROOT / ".config/mise/python-release",
            image=ROOT / ".config/ci/linux-release-image",
        )
    except (OSError, ValueError) as error:
        session.error(str(error))
    version = session.run(
        "python",
        "-c",
        "import platform; print(platform.python_version())",
        env=_environment(),
        silent=True,
    )
    if not (isinstance(version, str) and version.strip() == RELEASE_PYTHON == release_version):
        session.error("release interpreter differs from the immutable release runtime")


def _package_release_asset(session: nox.Session, bundle: Path, work: Path) -> None:
    """Export one manifest-bound native asset set after black-box acceptance."""
    output = Path(session.posargs[0]).resolve() if session.posargs else work / "release-assets"
    try:
        platform_id = product_identity.native_release_platform(
            platform.system(), platform.machine()
        )
    except ValueError as error:
        session.error(str(error))
    session.run(
        "python",
        "-m",
        "tools.release.artifact",
        "pack",
        "--bundle",
        str(bundle),
        "--platform",
        platform_id,
        "--output",
        str(output),
        env=_environment(),
    )


def _environment() -> dict[str, str]:
    """Return deterministic session settings and only explicitly selected overrides."""
    optional = (product_identity.environment_name("RELEASE_TAG_REMOTE"), "COVERAGE_FILE")
    return {
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONWARNINGS": "error",
        "SOURCE_DATE_EPOCH": "315532800",
        "UV_CONFIG_FILE": str(ROOT / ".config/mise/uv.toml"),
        "UV_NO_CONFIG": "false",
        **{name: os.environ[name] for name in optional if name in os.environ},
    }
