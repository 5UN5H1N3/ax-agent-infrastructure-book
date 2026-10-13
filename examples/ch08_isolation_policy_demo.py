"""Offline chapter 8 policy model. No sandbox or untrusted code is launched.

The profile catalog is a trusted fixture, not runtime attestation. Requests
describe one narrow external Python test workload, not AX/Kubernetes objects.
"""
from dataclasses import dataclass, replace
import json


class PolicyError(ValueError):
    """The narrow teaching policy cannot admit this request."""


@dataclass(frozen=True)
class Request:
    input_revision: str = "demo-fixture-v1"
    code_trust: str = "external"
    execution: str = "tests"
    toolchain: str = "python-demo"
    network: str = "none"
    capabilities: tuple[str, ...] = ()
    needs_guest_kernel: bool = False


@dataclass(frozen=True)
class Profile:
    name: str
    boundary: str
    toolchains: frozenset[str]


PROFILES = (
    Profile("review-gvisor-offline-v1", "system-api", frozenset({"python-demo"})),
    Profile("review-microvm-offline-v1", "guest-kernel", frozenset({"python-demo"})),
)


def validate_request(request):
    if type(request) is not Request:
        raise PolicyError("expected Request")
    if type(request.input_revision) is not str or not (
        1 <= len(request.input_revision) <= 128
        and request.input_revision.isascii()
        and all(c.isalnum() or c in "._-" for c in request.input_revision)
    ):
        raise PolicyError("invalid input revision label")
    for field, allowed in (
        ("code_trust", "external"), ("execution", "tests"),
        ("toolchain", "python-demo"), ("network", "none"),
    ):
        value = getattr(request, field)
        if type(value) is not str or value != allowed:
            raise PolicyError("unsupported " + field)
    if type(request.capabilities) is not tuple or request.capabilities:
        raise PolicyError("unsupported capabilities")
    if type(request.needs_guest_kernel) is not bool:
        raise PolicyError("needs_guest_kernel must be bool")


def select_profile(request, profiles):
    validate_request(request)
    required = (
        "guest-kernel" if request.needs_guest_kernel
        else "system-api"
    )
    matches = [
        profile for profile in profiles
        if profile.boundary == required
        and request.toolchain in profile.toolchains
    ]
    if not matches:
        raise PolicyError("no approved compatible profile")
    if len(matches) != 1:
        raise PolicyError("ambiguous profile catalog")
    return {
        "status": "plan_only",
        "input_revision": request.input_revision,
        "profile": matches[0].name,
        "boundary": required,
        "enforcement_verified": False,
    }


def main():
    baseline = Request()
    print(json.dumps(select_profile(baseline, PROFILES), indent=2))
    try:
        select_profile(replace(baseline, capabilities=("runtime-socket",)), PROFILES)
    except PolicyError as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}, indent=2))
    print(json.dumps(select_profile(replace(baseline, needs_guest_kernel=True), PROFILES), indent=2))


if __name__ == "__main__":
    main()
