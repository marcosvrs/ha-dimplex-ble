"""Enforce mutation score while excluding only confirmed equivalent mutants."""

import json
import re
import subprocess
import sys
from pathlib import Path

THRESHOLD = 99.0
STATS_PATH = Path("mutants/mutmut-cicd-stats.json")

EQUIVALENT_MUTANTS = frozenset(
    {
        "custom_components.dimplex_ble.protocol.x_parse_snapshot__mutmut_15",
        "custom_components.dimplex_ble.protocol.x_parse_snapshot__mutmut_34",
        "custom_components.dimplex_ble.client.x__create_pair_aware_client__mutmut_2",
        "custom_components.dimplex_ble.client.xǁDimplexBluetoothClientǁasync_read_raw__mutmut_3",
        "custom_components.dimplex_ble.client.xǁDimplexBluetoothClientǁasync_read_raw_fields__mutmut_3",
        "custom_components.dimplex_ble.pairing.x_proxy_supports_passkey__mutmut_19",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁasync_step_bluetooth__mutmut_4",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁasync_step_user__mutmut_26",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁasync_step_user__mutmut_18",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁasync_step_user__mutmut_33",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁasync_step_user__mutmut_41",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁasync_step_passkey__mutmut_30",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁasync_step_passkey__mutmut_71",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁasync_step_passkey__mutmut_80",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁasync_step_passkey__mutmut_83",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_46",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_47",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_51",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_52",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_54",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_56",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_57",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_58",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_59",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_60",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_61",
        "custom_components.dimplex_ble.config_flow.xǁDimplexBleConfigFlowǁ_async_retry_pairing_while_flow_active__mutmut_62",
    }
)
_SURVIVED_MUTANT_PATTERN = re.compile(r"^[ \t]*(\S+): survived$", re.MULTILINE)


def parse_surviving_mutants(output: str) -> set[str]:
    """Extract surviving mutant IDs from mutmut's results output."""
    return set(_SURVIVED_MUTANT_PATTERN.findall(output))


def calculate_mutation_score(
    stats: dict[str, int], surviving_mutants: set[str]
) -> tuple[int, float]:
    """Return score denominator and percentage, excluding known equivalents."""
    if stats["survived"] != len(surviving_mutants):
        raise ValueError("Mutmut survivor count does not match its results")

    unexpected = surviving_mutants - EQUIVALENT_MUTANTS
    if unexpected:
        raise ValueError(
            f"Unexpected surviving mutants: {', '.join(sorted(unexpected))}"
        )

    denominator = stats["total"] - len(surviving_mutants)
    if denominator <= 0:
        raise ValueError("Mutation score unavailable: no scored mutants")
    return denominator, 100 * stats["killed"] / denominator


def main() -> int:
    """Export and enforce mutmut's CI statistics."""
    subprocess.run(
        [sys.executable, "-m", "mutmut", "export-cicd-stats"], check=True
    )
    try:
        stats = json.loads(STATS_PATH.read_text())
    except (OSError, json.JSONDecodeError) as error:
        print(f"Unable to read mutation statistics at {STATS_PATH}: {error}", file=sys.stderr)
        return 1

    total = stats["total"]
    if total <= 0:
        print("Mutation score unavailable: no mutants were generated", file=sys.stderr)
        return 1

    result = subprocess.run(
        [sys.executable, "-m", "mutmut", "results", "--all", "true"],
        check=True,
        capture_output=True,
        text=True,
    )
    surviving_mutants = parse_surviving_mutants(result.stdout)
    try:
        scored_total, score = calculate_mutation_score(stats, surviving_mutants)
    except ValueError as error:
        print(f"Invalid mutation results: {error}", file=sys.stderr)
        return 1

    print(
        f"Mutation score: {stats['killed']}/{scored_total} = {score:.2f}% "
        f"({len(surviving_mutants)} equivalent mutants excluded; "
        f"required {THRESHOLD:.2f}%)"
    )
    return 0 if score >= THRESHOLD else 1


if __name__ == "__main__":
    raise SystemExit(main())
