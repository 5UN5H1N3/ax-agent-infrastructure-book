from dataclasses import replace
import json
import unittest

import ch41_inference_budget as m


class InferenceBudgetTests(unittest.TestCase):
    def setUp(self):
        self.memory = m.fixture_memory()

    def test_bf16_weight_bytes(self):
        self.assertEqual(m.packed_weight_bytes(8_000_000_000, 16), 16_000_000_000)

    def test_four_bit_is_packed_lower_bound(self):
        self.assertEqual(m.packed_weight_bytes(8_000_000_000, 4), 4_000_000_000)

    def test_packed_bits_round_up(self):
        self.assertEqual(m.packed_weight_bytes(3, 4), 2)

    def test_quantization_metadata_is_explicit(self):
        self.assertEqual(m.packed_weight_bytes(10, 4, 8), 13)

    def test_weight_input_validation(self):
        for args in [(0, 16), (10, 0), (10, 65), (True, 16), (10, 4, -1), (10.0, 16)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                m.packed_weight_bytes(*args)

    def test_kv_token_formula(self):
        self.assertEqual(m.Attention().bytes_per_token, 131072)

    def test_query_heads_cannot_replace_kv_heads(self):
        self.assertEqual(m.Attention(kv_heads=32).bytes_per_token,
                         4*m.Attention(kv_heads=8).bytes_per_token)

    def test_cache_dtype_separate_from_weights(self):
        half = replace(self.memory, attention=m.Attention(element_bytes=1))
        self.assertEqual(half.block_bytes, self.memory.block_bytes//2)
        self.assertEqual(half.weight_bytes, self.memory.weight_bytes)

    def test_unsupported_layout_rejected(self):
        for name in ('mla', 'sliding_window', 'hybrid'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                m.Attention(layout=name)

    def test_attention_dimension_validation(self):
        for field in ('layers', 'kv_heads', 'head_dim', 'element_bytes'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                m.Attention(**{field: 0})

    def test_block_rounding(self):
        self.assertEqual([m.blocks_for_tokens(n, 16) for n in (0, 1, 16, 17)], [0, 1, 1, 2])

    def test_block_input_validation(self):
        for args in [(-1, 16), (1, 0), (True, 16), (1, 1.5)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                m.blocks_for_tokens(*args)

    def test_budget_deducts_each_allocation_once(self):
        self.assertEqual(self.memory.kv_bytes, 2_790_481_920)
        self.assertEqual(self.memory.kv_blocks, 1330)
        self.assertEqual(self.memory.block_bytes, 2_097_152)

    def test_budget_rounds_down_to_whole_blocks(self):
        self.assertLessEqual(self.memory.kv_blocks*self.memory.block_bytes, self.memory.kv_bytes)
        self.assertGreater((self.memory.kv_blocks+1)*self.memory.block_bytes, self.memory.kv_bytes)

    def test_explicit_reserve_changes_memory_bound(self):
        self.assertEqual(self.memory.slots_at_length(8192), 2)
        self.assertEqual(m.fixture_memory(0).slots_at_length(8192), 3)

    def test_memory_cannot_create_space_by_increasing_context(self):
        self.assertEqual(self.memory.slots_at_length(32768), 0)

    def test_executor_cannot_exceed_device(self):
        with self.assertRaises(ValueError):
            replace(self.memory, executor_bytes=25*m.GIB)

    def test_negative_remaining_budget_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.memory, weight_bytes=22*m.GIB)

    def test_zero_cache_budget_is_explicit(self):
        memory = replace(self.memory, weight_bytes=17*m.GIB+m.GIB//2)
        self.assertEqual(memory.kv_blocks, 0)
        self.assertEqual(m.Admission(memory).admit(m.Request('a', 1, 1))['decision'], 'REJECT_KV')

    def test_sequence_count_and_token_validation(self):
        for args in [('', 1, 1), ('a', 0, 1), ('a', 1, 0), ('a', 1, 1, 0), ('a', True, 1)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                m.Request(*args)

    def test_reservation_includes_requested_output(self):
        request = m.Request('a', 7680, 512)
        self.assertEqual(request.reserved_tokens, 8192)
        self.assertEqual(m.Admission(self.memory).admit(request)['required_blocks'], 512)

    def test_two_fit_third_rejected(self):
        gate = m.Admission(self.memory)
        decisions = [gate.admit(m.Request(str(i), 7680, 512))['decision'] for i in range(3)]
        self.assertEqual(decisions, ['ACCEPT', 'ACCEPT', 'REJECT_KV'])
        self.assertEqual(gate.used_blocks, 1024)
        self.assertEqual(gate.used_sequences, 2)

    def test_rejection_leaves_state_unchanged(self):
        gate = m.Admission(self.memory)
        before = dict(gate.active)
        self.assertEqual(gate.admit(m.Request('a', 32768, 1))['decision'], 'REJECT_CONTEXT')
        self.assertEqual(gate.active, before)

    def test_long_pool_has_distinct_memory_rejection(self):
        gate = m.Admission(self.memory, max_context=32768)
        result = gate.admit(m.Request('a', 32256, 512))
        self.assertEqual(result['decision'], 'REJECT_KV')
        self.assertEqual(result['required_blocks'], 2048)

    def test_sequence_limit_is_independent_of_kv(self):
        gate = m.Admission(self.memory, max_sequences=1)
        gate.admit(m.Request('a', 1, 1))
        result = gate.admit(m.Request('b', 1, 1))
        self.assertEqual(result['decision'], 'REJECT_SEQUENCES')
        self.assertLess(gate.used_blocks, self.memory.kv_blocks)

    def test_multiple_sequences_round_separately(self):
        gate = m.Admission(self.memory)
        result = gate.admit(m.Request('a', 16, 1, 2))
        self.assertEqual(result['required_blocks'], 4)
        self.assertEqual(gate.used_sequences, 2)

    def test_duplicate_active_id_rejected(self):
        gate = m.Admission(self.memory)
        gate.admit(m.Request('a', 1, 1))
        with self.assertRaises(ValueError):
            gate.admit(m.Request('a', 1, 1))

    def test_release_restores_capacity(self):
        gate = m.Admission(self.memory)
        gate.admit(m.Request('a', 7680, 512))
        gate.admit(m.Request('b', 7680, 512))
        gate.release('a')
        self.assertEqual(gate.used_blocks, 512)
        self.assertEqual(gate.admit(m.Request('c', 7680, 512))['decision'], 'ACCEPT')

    def test_unknown_release_rejected(self):
        with self.assertRaises(ValueError):
            m.Admission(self.memory).release('missing')

    def test_admission_has_no_implicit_slo_claim(self):
        result = m.Admission(self.memory).admit(m.Request('a', 1, 1))
        self.assertFalse(result['slo_proven'])

    def test_invalid_admission_config(self):
        for args in [(None,), (self.memory, 0), (self.memory, 8192, 0)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                m.Admission(*args)

    def test_same_host_loses_both_replicas(self):
        replicas = [m.Replica('a', 'host', 2), m.Replica('b', 'host', 2)]
        result = m.loss_of_one_domain(replicas, 3)
        self.assertEqual(result['normal_rps'], 4)
        self.assertEqual(result['worst_remaining_rps'], 0)

    def test_two_hosts_still_underprovisioned_after_loss(self):
        replicas = [m.Replica('a', 'host-a', 2), m.Replica('b', 'host-b', 2)]
        result = m.loss_of_one_domain(replicas, 3)
        self.assertEqual(result['worst_remaining_rps'], 2)
        self.assertFalse(result['arithmetic_target_met'])

    def test_three_hosts_meet_only_arithmetic_target(self):
        replicas = [m.Replica(n, n, 2) for n in 'abc']
        result = m.loss_of_one_domain(replicas, 3)
        self.assertEqual(result['worst_remaining_rps'], 4)
        self.assertTrue(result['arithmetic_target_met'])
        self.assertFalse(result['production_capacity_proven'])

    def test_unequal_domain_loss_uses_worst_domain(self):
        replicas = [m.Replica('a', 'x', 3), m.Replica('b', 'y', 1), m.Replica('c', 'y', 1)]
        result = m.loss_of_one_domain(replicas, 2)
        self.assertEqual(result['after_domain_loss_rps'], {'x': 2, 'y': 3})
        self.assertEqual(result['worst_remaining_rps'], 2)

    def test_placement_validation(self):
        same = m.Replica('a', 'x', 2)
        for replicas, target in [([], 1), ([same, same], 1), ([same], 0)]:
            with self.subTest(replicas=replicas, target=target), self.assertRaises(ValueError):
                m.loss_of_one_domain(replicas, target)

    def test_replica_validation(self):
        for args in [('', 'x', 1), ('a', '', 1), ('a', 'x', 0), ('a', 'x', True)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                m.Replica(*args)

    def test_demo_output_is_reproducible_and_offline(self):
        result = m.demo()
        self.assertEqual(result, m.demo())
        self.assertEqual(result['scope'], 'OFFLINE_ARITHMETIC')
        self.assertFalse(result['gpu_benchmark_executed'])
        self.assertEqual(result['external_calls'], 0)
        self.assertEqual(result['memory']['kv_blocks'], 1330)
        self.assertEqual(result['admission']['r3']['decision'], 'REJECT_KV')
        json.dumps(result)


if __name__ == '__main__':
    unittest.main()
