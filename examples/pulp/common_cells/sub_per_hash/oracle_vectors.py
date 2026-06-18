"""Generate and verify deterministic golden vectors for the local sub_per_hash example.

Run from the repository root:

    uv run python examples/pulp/common_cells/sub_per_hash/oracle_vectors.py

This mirrors the local RTL adaptation used in rtl/sub_per_hash.sv and exists to
make the checked-in golden vectors reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass


DATA_WIDTH = 11
HASH_WIDTH = 5
NO_ROUNDS = 1

INPUT_VECTORS = (0, 1, 341, 1023)


@dataclass(frozen=True)
class SeedPair:
    label: str
    permute_key: int
    xor_key: int


SEEDS = (
    SeedPair("seed0", 299034753, 4094834),
    SeedPair("seed1", 19921030, 995713),
    SeedPair("seed2", 294388, 65146511),
)


EXPECTED = {
    0: {
        "seed0": (0, 0x00000001),
        "seed1": (0, 0x00000001),
        "seed2": (0, 0x00000001),
    },
    1: {
        "seed0": (0, 0x00000001),
        "seed1": (0, 0x00000001),
        "seed2": (0, 0x00000001),
    },
    341: {
        "seed0": (16, 0x00010000),
        "seed1": (0, 0x00000001),
        "seed2": (4, 0x00000010),
    },
    1023: {
        "seed0": (8, 0x00000100),
        "seed1": (26, 0x04000000),
        "seed2": (17, 0x00020000),
    },
}


def get_permutations(inp_width: int, no_rounds: int, seed: int) -> list[list[int]]:
    permutations = [[0] * inp_width for _ in range(no_rounds)]
    indices = [[0] * inp_width for _ in range(no_rounds)]
    a_perm = 2147483629
    c_perm = 2147483587
    m_perm = 2**31 - 1
    rand_perm = (a_perm * seed + c_perm) % m_perm

    for round_index in range(no_rounds):
        for bit_index in range(inp_width):
            indices[round_index][bit_index] = bit_index
            permutations[round_index][bit_index] = bit_index

        for bit_index in range(inp_width):
            if bit_index > 0:
                rand_perm = (a_perm * rand_perm + c_perm) % m_perm
                swap_index = rand_perm % bit_index
            else:
                swap_index = 0

            if bit_index != swap_index:
                permutations[round_index][bit_index] = permutations[round_index][swap_index]
                permutations[round_index][swap_index] = indices[round_index][bit_index]

        rand_perm = (a_perm * rand_perm + c_perm) % m_perm
        advance = rand_perm % no_rounds
        for _ in range(advance):
            rand_perm = (a_perm * rand_perm + c_perm) % m_perm

    return permutations


def get_xor_stages(inp_width: int, no_rounds: int, seed: int) -> list[list[list[int]]]:
    xor_stages = [[[0] * 3 for _ in range(inp_width)] for _ in range(no_rounds)]
    a_xor = 1664525
    c_xor = 1013904223
    m_xor = 2**32
    rand_xor = (a_xor * seed + c_xor) % m_xor

    for round_index in range(no_rounds):
        for bit_index in range(inp_width):
            for xor_index in range(3):
                rand_xor = (a_xor * rand_xor + c_xor) % m_xor
                select_index = rand_xor % inp_width
                xor_stages[round_index][bit_index][xor_index] = select_index

        rand_xor = (a_xor * rand_xor + c_xor) % m_xor
        advance = rand_xor % no_rounds
        for _ in range(advance):
            rand_xor = (a_xor * rand_xor + c_xor) % m_xor

    return xor_stages


def compute_hash(data: int, seed_pair: SeedPair) -> tuple[int, int]:
    permutations = get_permutations(DATA_WIDTH, NO_ROUNDS, seed_pair.permute_key)
    xor_stages = get_xor_stages(DATA_WIDTH, NO_ROUNDS, seed_pair.xor_key)
    previous_stage = [(data >> bit_index) & 1 for bit_index in range(DATA_WIDTH)]

    for round_index in range(NO_ROUNDS):
        permuted = [0] * DATA_WIDTH
        xored = [0] * DATA_WIDTH
        for bit_index in range(DATA_WIDTH):
            permuted[bit_index] = previous_stage[permutations[round_index][bit_index]]
            src_a, src_b, src_c = xor_stages[round_index][bit_index]
            xored[bit_index] = permuted[src_a] ^ permuted[src_b] ^ permuted[src_c]
        previous_stage = xored

    hash_value = 0
    for bit_index in range(HASH_WIDTH):
        hash_value |= (previous_stage[bit_index] & 1) << bit_index
    onehot_value = 1 << hash_value
    return hash_value, onehot_value


def main() -> int:
    computed: dict[int, dict[str, tuple[int, int]]] = {}
    for data in INPUT_VECTORS:
        computed[data] = {}
        for seed_pair in SEEDS:
            computed[data][seed_pair.label] = compute_hash(data, seed_pair)

    mismatches: list[str] = []
    for data, expected_by_seed in EXPECTED.items():
        for seed_label, expected_pair in expected_by_seed.items():
            actual_pair = computed[data][seed_label]
            if actual_pair != expected_pair:
                mismatches.append(f"data={data} {seed_label}: expected={expected_pair} actual={actual_pair}")

    print("Computed golden vectors:")
    for data in INPUT_VECTORS:
        print(f"  data={data}")
        for seed_pair in SEEDS:
            hash_value, onehot_value = computed[data][seed_pair.label]
            print(f"    {seed_pair.label}: hash={hash_value:2d} onehot=0x{onehot_value:08X}")

    if mismatches:
        print("\nMismatches:")
        for mismatch in mismatches:
            print(f"  {mismatch}")
        return 1

    print("\nGolden vectors match the checked-in wrapper expectations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
