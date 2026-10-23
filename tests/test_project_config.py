"""R1: pyproject.toml's runtime dependencies must match requirements.txt's pins.

requirements.txt stays Render's build input (R5); pyproject.toml is what uv and
the kit use. If the two drift, local checks and production run different code.

Stdlib only, so the module also imports under `python -m unittest discover`.
"""

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
REQUIREMENTS = REPO_ROOT / "requirements.txt"

# name, optional whitespace, "==", version; nothing else (no extras, markers or ranges).
PIN = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*==\s*([^\s=;#,]+)$")


def normalise_name(name):
    """PEP 503: lowercase, and every run of -, _ and . becomes one -."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pin(spec, source):
    match = PIN.match(spec.strip())
    if not match:
        raise ValueError(f"{source}: {spec!r} is not an exact `name==version` pin")
    return normalise_name(match.group(1)), match.group(2)


def add_pin(pins, spec, source):
    name, version = parse_pin(spec, source)
    if name in pins:
        raise ValueError(f"{source}: {name} is declared more than once")
    pins[name] = version


def requirements_pins(text, source="requirements.txt"):
    pins = {}
    for line in text.splitlines():
        spec = line.split("#", 1)[0].strip()
        if spec:
            add_pin(pins, spec, source)
    return pins


def pyproject_pins(text, source="pyproject.toml"):
    project = tomllib.loads(text).get("project")
    if not isinstance(project, dict) or "dependencies" not in project:
        raise ValueError(f"{source}: no [project].dependencies list")
    pins = {}
    for spec in project["dependencies"]:
        add_pin(pins, spec, source)
    return pins


def pin_mismatches(pyproject, requirements):
    """Every difference between two name->version maps, one message each."""
    problems = []
    for name in sorted(set(pyproject) | set(requirements)):
        if name not in pyproject:
            problems.append(f"{name}: in requirements.txt only ({requirements[name]})")
        elif name not in requirements:
            problems.append(f"{name}: in pyproject.toml only ({pyproject[name]})")
        elif pyproject[name] != requirements[name]:
            problems.append(
                f"{name}: pyproject.toml pins {pyproject[name]}, "
                f"requirements.txt pins {requirements[name]}"
            )
    return problems


# --- the real files (R1) -------------------------------------------------------------------------


def test_pyproject_dependencies_match_requirements_txt():
    assert REQUIREMENTS.is_file(), f"{REQUIREMENTS} is missing"
    assert PYPROJECT.is_file(), (
        f"{PYPROJECT} is missing: R1 needs the runtime pins from requirements.txt "
        "declared in its [project].dependencies"
    )
    problems = pin_mismatches(
        pyproject_pins(PYPROJECT.read_text(encoding="utf-8")),
        requirements_pins(REQUIREMENTS.read_text(encoding="utf-8")),
    )
    assert problems == [], (
        "pyproject.toml and requirements.txt disagree:\n" + "\n".join(problems)
    )


# --- the helpers, on synthetic input (proves the check bites) ------------------------------------


def test_normalise_name_follows_pep_503():
    assert normalise_name("Pillow") == "pillow"
    assert normalise_name("pillow_heif") == "pillow-heif"
    assert normalise_name("Pillow-HEIF") == "pillow-heif"
    assert normalise_name("Some._-Package") == "some-package"


def test_requirements_parser_ignores_blank_lines_and_comments():
    text = (
        "# full-line comment\n"
        "\n"
        "Alpha_Pkg==1.2.3\n"
        "   \n"
        "beta.pkg == 4.5   # inline comment, with == inside it\n"
        "    # indented comment\n"
        "gamma==0.1.0rc1\n"
    )
    assert requirements_pins(text) == {
        "alpha-pkg": "1.2.3",
        "beta-pkg": "4.5",
        "gamma": "0.1.0rc1",
    }


def test_parsers_reject_anything_but_exact_pins():
    for bad in ("alpha>=1.0", "alpha", "alpha==1.0,<2", "alpha~=1.0"):
        try:
            requirements_pins(bad + "\n")
        except ValueError as err:
            assert bad in str(err)
        else:
            raise AssertionError(f"{bad!r} was accepted as a pin")
    try:
        requirements_pins("alpha==1.0\nAlpha==1.0\n")
    except ValueError as err:
        assert "more than once" in str(err)
    else:
        raise AssertionError("a duplicate name was accepted")


def test_pyproject_parser_needs_a_dependencies_list():
    for text in ("", '[project]\nname = "synthetic"\n', "[tool.other]\nx = 1\n"):
        try:
            pyproject_pins(text)
        except ValueError as err:
            assert "[project].dependencies" in str(err)
        else:
            raise AssertionError(
                f"{text!r} was accepted without [project].dependencies"
            )


def test_matching_files_report_no_mismatch():
    pyproject = '[project]\nname = "synthetic"\ndependencies = ["Alpha_Pkg==1.0.0", "beta==2.0"]\n'
    requirements = "beta==2.0\nalpha-pkg==1.0.0  # a comment\n"
    assert (
        pin_mismatches(pyproject_pins(pyproject), requirements_pins(requirements)) == []
    )


def test_drifted_files_report_every_mismatch():
    pyproject = (
        '[project]\nname = "synthetic"\n'
        'dependencies = ["alpha==1.0.0", "beta==2.0.1", "delta==4.0"]\n'
    )
    requirements = "alpha==1.0.0\nbeta==2.0.0\ngamma==3.0  # only here\n"
    problems = pin_mismatches(
        pyproject_pins(pyproject), requirements_pins(requirements)
    )
    assert problems == [
        "beta: pyproject.toml pins 2.0.1, requirements.txt pins 2.0.0",
        "delta: in pyproject.toml only (4.0)",
        "gamma: in requirements.txt only (3.0)",
    ]
