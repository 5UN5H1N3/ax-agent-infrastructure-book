"""Offline full-attention memory accounting, NOT a GPU benchmark/scheduler.

Uniform full-attention layers, one device, no prefix sharing, no offload.
Reserve the worst-case prompt + requested output for every sequence; real
serving schedulers may allocate incrementally, preempt or share blocks.
Fixture rates in the placement exercise are assumptions, not measurements.
No model, tokenizer, GPU, network or AX/Substrate client is invoked.
"""
from dataclasses import dataclass
import json


GIB = 1 << 30
MIB = 1 << 20


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def packed_weight_bytes(parameters, bits, metadata_bytes=0):
    """Packed tensor lower bound plus explicitly supplied extra resident bytes."""
    if not integer(parameters, 1) or not integer(bits, 1) or bits > 64:
        raise ValueError('positive parameters and bits in 1..64 required')
    if not integer(metadata_bytes):
        raise ValueError('metadata bytes must be a nonnegative integer')
    return (parameters * bits + 7) // 8 + metadata_bytes


@dataclass(frozen=True)
class Attention:
    layers: int = 32
    kv_heads: int = 8
    head_dim: int = 128
    element_bytes: int = 2
    layout: str = 'uniform_full_attention'

    def __post_init__(self):
        if self.layout != 'uniform_full_attention':
            raise ValueError('only uniform full attention is modeled')
        if not all(integer(v, 1) for v in
                   (self.layers, self.kv_heads, self.head_dim, self.element_bytes)):
            raise ValueError('attention dimensions must be positive integers')

    @property
    def bytes_per_token(self):
        return 2 * self.layers * self.kv_heads * self.head_dim * self.element_bytes


def blocks_for_tokens(tokens, block_tokens):
    if not integer(tokens) or not integer(block_tokens, 1):
        raise ValueError('nonnegative tokens and positive block size required')
    return (tokens + block_tokens - 1) // block_tokens


@dataclass(frozen=True)
class Memory:
    device_bytes: int
    executor_bytes: int
    weight_bytes: int
    other_bytes: int
    reserve_bytes: int
    attention: Attention = Attention()
    block_tokens: int = 16

    def __post_init__(self):
        if not isinstance(self.attention, Attention):
            raise ValueError('Attention required')
        if not all(integer(v) for v in (self.device_bytes, self.executor_bytes,
                   self.weight_bytes, self.other_bytes, self.reserve_bytes)):
            raise ValueError('memory budgets must be nonnegative integer bytes')
        if not 0 < self.executor_bytes <= self.device_bytes:
            raise ValueError('executor must fit in a positive device budget')
        if not integer(self.block_tokens, 1):
            raise ValueError('positive block size required')
        if self.kv_bytes < 0:
            raise ValueError('weights, other allocations and reserve exceed executor budget')

    @property
    def kv_bytes(self):
        return self.executor_bytes - self.weight_bytes - self.other_bytes - self.reserve_bytes

    @property
    def block_bytes(self):
        return self.block_tokens * self.attention.bytes_per_token

    @property
    def kv_blocks(self):
        return self.kv_bytes // self.block_bytes

    def slots_at_length(self, total_tokens):
        if not integer(total_tokens, 1):
            raise ValueError('positive sequence length required')
        return self.kv_blocks // blocks_for_tokens(total_tokens, self.block_tokens)


@dataclass(frozen=True)
class Request:
    request_id: str
    prompt_tokens: int
    max_new_tokens: int
    sequences: int = 1

    def __post_init__(self):
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError('nonempty request ID required')
        if not all(integer(v, 1) for v in
                   (self.prompt_tokens, self.max_new_tokens, self.sequences)):
            raise ValueError('positive integer token limits and sequence count required')

    @property
    def reserved_tokens(self):
        return self.prompt_tokens + self.max_new_tokens


class Admission:
    """Serial, no-queue, conservative memory/sequence gate; no latency estimate."""
    def __init__(self, memory, max_context=8192, max_sequences=3):
        if not isinstance(memory, Memory):
            raise ValueError('Memory required')
        if not integer(max_context, 1) or not integer(max_sequences, 1):
            raise ValueError('positive context and sequence limits required')
        self.memory = memory
        self.max_context, self.max_sequences = max_context, max_sequences
        self.active = {}

    @property
    def used_blocks(self):
        return sum(blocks for blocks, sequences in self.active.values())

    @property
    def used_sequences(self):
        return sum(sequences for blocks, sequences in self.active.values())

    def admit(self, request):
        if not isinstance(request, Request):
            raise ValueError('Request required')
        if request.request_id in self.active:
            raise ValueError('duplicate active request ID')
        blocks = blocks_for_tokens(request.reserved_tokens, self.memory.block_tokens) * request.sequences
        if request.reserved_tokens > self.max_context:
            decision = 'REJECT_CONTEXT'
        elif self.used_sequences + request.sequences > self.max_sequences:
            decision = 'REJECT_SEQUENCES'
        elif self.used_blocks + blocks > self.memory.kv_blocks:
            decision = 'REJECT_KV'
        else:
            self.active[request.request_id] = (blocks, request.sequences)
            decision = 'ACCEPT'
        return {'decision': decision, 'required_blocks': blocks,
                'used_blocks': self.used_blocks, 'used_sequences': self.used_sequences,
                'slo_proven': False}

    def release(self, request_id):
        """Call after observed backend completion, not merely client disconnect."""
        if request_id not in self.active:
            raise ValueError('unknown active request ID')
        del self.active[request_id]


@dataclass(frozen=True)
class Replica:
    name: str
    domain: str
    assumed_slo_rps: int

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in (self.name, self.domain)):
            raise ValueError('replica and failure-domain names required')
        if not integer(self.assumed_slo_rps, 1):
            raise ValueError('positive integer fixture rate required')


def loss_of_one_domain(replicas, target_rps):
    replicas = tuple(replicas)
    if not replicas or not all(isinstance(r, Replica) for r in replicas):
        raise ValueError('nonempty Replica collection required')
    if len({r.name for r in replicas}) != len(replicas):
        raise ValueError('duplicate replica name')
    if not integer(target_rps, 1):
        raise ValueError('positive integer target rate required')
    normal = sum(r.assumed_slo_rps for r in replicas)
    remaining = {domain: sum(r.assumed_slo_rps for r in replicas if r.domain != domain)
                 for domain in sorted({r.domain for r in replicas})}
    worst = min(remaining.values())
    return {'normal_rps': normal, 'after_domain_loss_rps': remaining,
            'worst_remaining_rps': worst, 'target_rps': target_rps,
            'arithmetic_target_met': worst >= target_rps,
            'production_capacity_proven': False}


def fixture_memory(reserve_bytes=GIB//2):
    return Memory(24*GIB, 21*GIB, packed_weight_bytes(8_000_000_000, 16),
                  3*GIB, reserve_bytes)


def demo():
    memory = fixture_memory()
    gate = Admission(memory)
    decisions = {}
    for i in range(1, 4):
        decisions[f'r{i}'] = gate.admit(Request(f'r{i}', 7680, 512))
    gate.release('r1')
    decisions['after_release'] = gate.admit(Request('r4', 7680, 512))
    decisions['too_long'] = gate.admit(Request('long', 32256, 512))
    decisions['long_pool_same_device'] = Admission(memory, max_context=32768).admit(
        Request('long', 32256, 512))
    placements = {
        'two_same_host': (Replica('a', 'host-a', 2), Replica('b', 'host-a', 2)),
        'two_hosts': (Replica('a', 'host-a', 2), Replica('b', 'host-b', 2)),
        'three_hosts': tuple(Replica(name, 'host-'+name, 2) for name in 'abc'),
    }
    return {
        'memory': {'weight_bytes': memory.weight_bytes,
                   'kv_bytes_per_token': memory.attention.bytes_per_token,
                   'block_bytes': memory.block_bytes, 'kv_budget_bytes': memory.kv_bytes,
                   'kv_blocks': memory.kv_blocks,
                   'max_8192_sequences': memory.slots_at_length(8192),
                   'without_reserve_8192_sequences': fixture_memory(0).slots_at_length(8192)},
        'admission': decisions,
        'placement': {name: loss_of_one_domain(replicas, 3)
                      for name, replicas in placements.items()},
        'scope': 'OFFLINE_ARITHMETIC', 'gpu_benchmark_executed': False,
        'external_calls': 0,
    }


if __name__ == '__main__':
    print(json.dumps(demo(), indent=2))
