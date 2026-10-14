"""Repository-owned verification and native-distribution sessions."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import platform
import re
import sys
import tomllib
from pathlib import Path
from pathlib import PurePosixPath
from tempfile import TemporaryDirectory

import nox

from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import artifact

ROOT = Path(__file__).parent.resolve()
_python_quality_loader = importlib.machinery.SourceFileLoader(
    "proxy_python_quality", str(ROOT / "tools/quality/python.py")
)
_python_quality_spec = importlib.machinery.ModuleSpec(
    _python_quality_loader.name, _python_quality_loader, origin=_python_quality_loader.path
)
_python_quality_spec.has_location = True
python_quality = importlib.util.module_from_spec(_python_quality_spec)
_python_quality_loader.exec_module(python_quality)
PYTHONS = tuple((ROOT / ".python-versions").read_text(encoding="utf-8").splitlines())
MIN_PYTHON, *_, MAX_PYTHON = PYTHONS
RELEASE_PYTHON = (ROOT / ".python-release").read_text(encoding="utf-8").strip()
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
    _install_tools(session)
    python_quality.static(session, environment=_environment(), minimum_python=MIN_PYTHON)
    session.run(*PYTEST_COMMAND, "tests/quality/test_contract.py", env=_environment())


@nox.session(python=PYTHONS)
def tests(session: nox.Session) -> None:
    """Compile and run the complete behavior inventory on one Python."""
    _install_tools(session)
    work = Path(session.create_tmp()).resolve()
    wheel = _build_wheel(session, work)
    _install_wheel(session, wheel)
    _assert_installed_product(session, work)
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
    _install_tools(session)
    work = Path(session.create_tmp()).resolve()
    wheel = _build_wheel(session, work)
    _install_wheel(session, wheel)
    _assert_installed_product(session, work)
    environment: dict[str, str | None] = {
        **_environment(),
        product_identity.environment_name("EXECUTABLE"): str(_installed_executable(session)),
    }
    python_quality.static(session, environment=_environment(), minimum_python=MIN_PYTHON)
    python_quality.behavior(session, environment=environment, require_coverage=True)
    if os.environ.get("ETHOS_NATIVE_OUTPUT_DIR"):
        session.run(
            *PYTEST_COMMAND,
            "tests/quality/orchestration/test_sessions.py",
            "-k",
            "native_behavior_reports_share_one_actual_pytest_attempt "
            "or native_static_report_observes_one_original_ruff_launch",
            env={**environment, "ETHOS_NATIVE_OUTPUT_DIR": None},
        )


@nox.session(python=RELEASE_PYTHON)
def performance(session: nox.Session) -> None:
    """Measure deterministic product overhead and emit machine-readable evidence."""
    _install_tools(session)
    work = Path(session.create_tmp()).resolve()
    wheel = _build_wheel(session, work)
    _install_wheel(session, wheel)
    _assert_installed_product(session, work)
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
def full(session: nox.Session) -> None:
    """Run governance, strict quality, and every remaining supported interpreter."""
    session.notify("governance")
    session.notify("quality")
    for python in PYTHONS[1:]:
        session.notify(f"tests-{python}")


@nox.session(python=RELEASE_PYTHON)
def release_asset(session: nox.Session) -> None:
    """Build and accept a native asset without requiring a host service manager."""
    work, bundle, executable = _build_native_candidate(session)
    environment = {
        **_environment(),
        product_identity.environment_name("EXECUTABLE"): str(executable),
        product_identity.environment_name("NATIVE_EXECUTABLE"): str(executable),
    }
    session.run(
        *PYTEST_COMMAND,
        "tests/cli/test_interface.py",
        "tests/serving/handoff/test_subprocess.py",
        env=environment,
    )
    _package_release_asset(session, bundle, work)


@nox.session(python=RELEASE_PYTHON)
def release(session: nox.Session) -> None:
    """Build and accept this platform's asset and native service lifecycle."""
    work, bundle, executable = _build_native_candidate(session)
    environment = {
        **_environment(),
        product_identity.environment_name("EXECUTABLE"): str(executable),
        product_identity.environment_name("NATIVE_EXECUTABLE"): str(executable),
    }
    session.run(
        *PYTEST_COMMAND,
        "tests/cli/test_interface.py",
        "tests/release/test_native_lifecycle.py",
        "tests/serving/handoff/test_subprocess.py",
        env=environment,
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
    session.run(
        *PYTEST_COMMAND,
        "tests/release/test_native_compatibility.py",
        env={
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
    _install_tools(session, "quality")
    _assert_release_runtime(session)
    work = Path(session.create_tmp()).resolve()
    work.mkdir(parents=True, exist_ok=True)
    wheel = _build_wheel(session, work)
    _install_wheel(session, wheel)
    _assert_installed_product(session, work)
    bundle, executable = _materialize_published_bundle(session, current_asset, trust, work)
    session.run(
        *PYTEST_COMMAND,
        "tests/cli/test_interface.py",
        "tests/serving/handoff/test_subprocess.py",
        "tests/release/test_native_lifecycle.py",
        "tests/release/test_native_compatibility.py",
        *session.posargs,
        env={
            **_environment(),
            product_identity.environment_name("EXECUTABLE"): str(executable),
            product_identity.environment_name("NATIVE_EXECUTABLE"): str(executable),
            product_identity.environment_name("NATIVE_BUNDLE"): str(bundle),
            product_identity.environment_name("PREVIOUS_RELEASE_ASSET"): str(previous_asset),
            product_identity.environment_name("PREVIOUS_RELEASE_TRUST_ANCHOR"): str(previous_trust),
        },
    )


def _materialize_published_bundle(
    session: nox.Session,
    release_asset: Path,
    trust_anchor: Path,
    work: Path,
) -> tuple[Path, Path]:
    """Materialize one verified published bundle for native execution."""
    released = artifact.admit(release_asset, trust_anchor=trust_anchor)
    expected_version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
    if released.version != expected_version:
        session.error("published release asset version does not match the checkout")
    expected_platform = product_identity.native_release_platform(
        platform.system(), platform.machine()
    )
    if released.receipt.get("platform") != expected_platform:
        session.error("published release asset does not match the native host")
    bundle = work / "published-release"
    bundle.mkdir()
    for blob in released.peek_blobs():
        target = bundle.joinpath(*PurePosixPath(blob.path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.content)
        target.chmod(0o755 if blob.mode == "100755" else 0o644)
    executable = bundle / "bin" / product_identity.executable_name(windows=os.name == "nt")
    if not executable.is_file():
        session.error("published release executable was not materialized")
    return executable.parent, executable


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
    session.run_install(
        *command,
        silent=True,
        env={"PYTHONNOUSERSITE": "1", "UV_NO_PROGRESS": "1"},
        external=True,
    )
    session.install(
        "--requirements",
        str(requirements),
        "--strict",
        env={"PYTHONNOUSERSITE": "1", "UV_NO_PROGRESS": "1"},
    )
    python_quality.install_native_helpers(session)


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
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    image = metadata["tool"][product_identity.PRODUCT_SLUG]["linux-release-image"]
    match = re.search(r"python:(\d+\.\d+\.\d+)-", image)
    version = session.run(
        "python",
        "-c",
        "import platform; print(platform.python_version())",
        env=_environment(),
        silent=True,
    )
    if (
        match is None
        or RELEASE_PYTHON != match.group(1)
        or not isinstance(version, str)
        or version.strip() != RELEASE_PYTHON
    ):
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
    """Return the deterministic environment shared by every session."""
    return {
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONWARNINGS": "error",
        "SOURCE_DATE_EPOCH": "315532800",
        "UV_LINK_MODE": "copy",
        **(
            {
                product_identity.environment_name("RELEASE_TAG_REMOTE"): os.environ[
                    product_identity.environment_name("RELEASE_TAG_REMOTE")
                ]
            }
            if product_identity.environment_name("RELEASE_TAG_REMOTE") in os.environ
            else {}
        ),
        **({"COVERAGE_FILE": os.environ["COVERAGE_FILE"]} if "COVERAGE_FILE" in os.environ else {}),
    }
