"""Synthetic delivery and HTTP mocks only; no real model weights or inference."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from examples.learning_harness import harness
from examples.model_profile import profile as p


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile=p.load_profile()
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)

    def delivery(self):
        profile=deepcopy(self.profile)
        profile['id']='synthetic-delivery-fixture'
        metadata=profile['gguf_metadata']
        config={field:metadata[key] for field,key in (
            ('num_hidden_layers','layers'),('num_attention_heads','query_heads'),
            ('num_key_value_heads','kv_heads'),('head_dim','head_dim'),
            ('hidden_size','hidden_size'),('max_position_embeddings','context_length'))}
        template='SYNTHETIC TEMPLATE: not a model'
        metadata['reference_template_sha256']=p.digest(template.encode())
        for item in profile['files']:
            if item['name'].endswith('.gguf'):
                raw=b'GGUF\x03\x00\x00\x00SYNTHETIC FILE, NO TENSORS'
            elif item['name']=='source-config.json':
                raw=json.dumps(config).encode()
            elif item['name']=='source-tokenizer_config.json':
                raw=json.dumps({'chat_template':template}).encode()
            else:
                raw=b'Synthetic sidecar, not an upstream license or card'
            item['bytes']=len(raw)
            item['sha256']=p.digest(raw)
            (self.root/item['name']).write_bytes(raw)
        return profile

    def test_default_profile_is_a_candidate_not_a_result(self):
        self.assertEqual(self.profile['artifact_revision'],'23749fefcc72300e3a2ad315e1317431b06b590a')
        self.assertIsNone(self.profile['conversion_parent_revision'])
        self.assertFalse(self.profile['author_review']['runtime_built'])
        self.assertFalse(self.profile['author_review']['full_model_checksum_checked_locally'])
        self.assertEqual(self.profile['admission']['quality'],'NOT_RUN')
        self.assertNotEqual(self.profile['gguf_metadata']['embedded_template_sha256'],
                            self.profile['gguf_metadata']['reference_template_sha256'])

    def test_memory_uses_explicit_head_dim_not_hidden_div_heads(self):
        sheet=p.memory_sheet(self.profile)
        self.assertEqual(sheet['ideal_f16_kv_bytes_one_slot'],939524096)
        self.assertEqual(sheet['file_plus_ideal_kv_bytes'],1578970784)
        metadata=self.profile['gguf_metadata']
        self.assertNotEqual(metadata['head_dim'],metadata['hidden_size']//metadata['query_heads'])
        self.assertIsNone(sheet['measured_peak_ram_bytes'])
        self.assertIsNone(sheet['measured_ttft_seconds'])

    def test_memory_scales_with_context(self):
        self.profile['runtime']['context_tokens']=4096
        self.assertEqual(p.memory_sheet(self.profile)['ideal_f16_kv_bytes_one_slot'],469762048)

    def test_plan_has_pins_no_automatic_process_or_network(self):
        with patch.object(harness,'transport') as transport:
            result=p.plan(self.profile)
            transport.assert_not_called()
        self.assertEqual(len(result['download_commands_not_executed']),8)
        self.assertIn('--reasoning off',result['server_command_not_executed'])
        self.assertIn('--gpu-layers 0',result['server_command_not_executed'])
        self.assertNotIn('--chat-template-file',result['server_command_not_executed'])
        self.assertIn(self.profile['runtime']['commit'],' '.join(result['build_commands_not_executed']))
        self.assertNotIn('/resolve/main/',' '.join(result['download_commands_not_executed']))

    def test_changed_harness_hash_is_rejected(self):
        self.profile['harness']['source_sha256']['examples/learning_harness/harness.py']='0'*64
        with self.assertRaisesRegex(p.ProfileError,'changed'):
            p.check_code(self.profile)

    def test_profile_json_is_bounded_and_duplicate_keys_rejected(self):
        for raw in ('x'*65537,'{"schema_version":1,"schema_version":1}'):
            path=self.root/'profile.json'
            path.write_text(raw)
            with self.assertRaises(p.ProfileError):
                p.load_profile(path)

    def test_source_revision_must_be_full(self):
        for key in ('artifact_revision','source_revision_reference'):
            copy=deepcopy(self.profile)
            copy[key]='main'
            with self.subTest(key=key),self.assertRaises(p.ProfileError):
                p.validate(copy)

    def test_runtime_commit_and_context_are_explicit(self):
        for key,value in (('commit','main'),('context_tokens',50000),('context_tokens',True),
                          ('context_tokens',512),('parallel_slots',True),('parallel_slots',2),
                          ('reasoning','auto'),('backend','GPU'),('host','0.0.0.0')):
            copy=deepcopy(self.profile)
            copy['runtime'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(p.ProfileError):
                p.validate(copy)

    def test_plan_rejects_changed_repository_and_untyped_options(self):
        for key,value in (('repository','https://example.org/repo.git; echo unsafe'),
                          ('threads',0),('threads',True),('top_k',True),('seed',False),
                          ('jinja','yes')):
            copy=deepcopy(self.profile)
            copy['runtime'][key]=value
            with self.subTest(key=key),self.assertRaises(p.ProfileError):
                p.validate(copy)

    def test_file_url_must_match_the_recorded_revision(self):
        self.profile['files'][0]['url']=self.profile['files'][0]['url'].replace(
            self.profile['artifact_revision'],'a'*40)
        with self.assertRaisesRegex(p.ProfileError,'repository/revision'):
            p.validate(self.profile)

    def test_finite_sampling_and_deadline_required(self):
        for key in ('temperature','top_p','min_p','presence_penalty'):
            self.profile['runtime'][key]=float('nan')
            with self.assertRaises(p.ProfileError):
                p.validate(self.profile)
            self.profile=p.load_profile()
        for value in (True,0,-1,301,float('inf')):
            self.profile['harness']['seconds_per_case']=value
            with self.subTest(value=value),self.assertRaises(p.ProfileError):
                p.validate(self.profile)

    def test_delivery_requires_plain_unique_names_and_pinned_urls(self):
        for key,value in (('name','../secret'),('name','/etc/passwd'),
                          ('url','https://huggingface.co/model/resolve/main/a'),
                          ('url','http://huggingface.co/model/resolve/'+'a'*40+'/a'),
                          ('bytes',True),('sha256','bad')):
            copy=deepcopy(self.profile)
            copy['files'][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(p.ProfileError):
                p.validate(copy)
        self.profile['files'].append(deepcopy(self.profile['files'][0]))
        with self.assertRaises(p.ProfileError):
            p.validate(self.profile)

    def test_missing_sidecar_is_rejected(self):
        self.profile['files'].pop()
        with self.assertRaisesRegex(p.ProfileError,'sidecars'):
            p.validate(self.profile)

    def test_toy_delivery_verifies_bytes_only(self):
        profile=self.delivery()
        result=p.check_delivery(profile,self.root)
        self.assertEqual(result['delivery'],'VERIFIED_LOCAL_BYTES')
        self.assertEqual(len(result['checked_files']),6)
        self.assertEqual(result['interface'],'NOT_RUN')
        self.assertEqual(result['quality'],'NOT_RUN')
        self.assertEqual(result['runtime_identity'],'NOT_RECORDED')
        self.assertTrue(result['reference_template_differs'])

    def test_wrong_size_and_sha_fail(self):
        profile=self.delivery()
        path=self.root/'MODEL_CARD.md'
        original=path.read_bytes()
        for raw in (b'x',b'x'*len(original)):
            path.write_bytes(raw)
            with self.subTest(size=len(raw)),self.assertRaises(p.ProfileError):
                p.check_delivery(profile,self.root)

    def test_safetensors_cannot_be_renamed_gguf(self):
        profile=self.delivery()
        item=p.model_file(profile)
        raw=b'\x00\x00\x00\x00\x00\x00\x00\x00' + b'synthetic safetensors'
        item.update(bytes=len(raw),sha256=p.digest(raw))
        (self.root/item['name']).write_bytes(raw)
        with self.assertRaisesRegex(p.ProfileError,'GGUF v3'):
            p.check_delivery(profile,self.root)

    def test_symlinks_and_fifo_are_rejected(self):
        profile=self.delivery()
        path=self.root/'MODEL_CARD.md'
        backup=self.root/'backup'
        path.rename(backup)
        path.symlink_to(backup)
        with self.assertRaises(OSError):
            p.check_delivery(profile,self.root)
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(p.ProfileError,'regular'):
            p.check_delivery(profile,self.root)

    def test_wrong_reference_architecture_fails_even_with_new_file_hash(self):
        profile=self.delivery()
        item=next(item for item in profile['files'] if item['name']=='source-config.json')
        path=self.root/item['name']
        config=json.loads(path.read_text())
        config['head_dim']=64
        raw=json.dumps(config).encode()
        path.write_bytes(raw)
        item.update(bytes=len(raw),sha256=p.digest(raw))
        with self.assertRaisesRegex(p.ProfileError,'head_dim'):
            p.check_delivery(profile,self.root)

    def test_wrong_reference_template_fails_even_with_new_file_hash(self):
        profile=self.delivery()
        item=next(item for item in profile['files'] if item['name']=='source-tokenizer_config.json')
        raw=json.dumps({'chat_template':'new unreviewed template'}).encode()
        (self.root/item['name']).write_bytes(raw)
        item.update(bytes=len(raw),sha256=p.digest(raw))
        with self.assertRaisesRegex(p.ProfileError,'template'):
            p.check_delivery(profile,self.root)

    def test_default_cli_no_network_and_no_model(self):
        env=os.environ.copy()
        env['PYTHONDONTWRITEBYTECODE']='1'
        process=subprocess.run([sys.executable,'-m','examples.model_profile.profile'],cwd=p.ROOT,
                               env=env,capture_output=True,text=True,check=True)
        result=json.loads(process.stdout)
        self.assertEqual(result['note'],'No model downloaded, loaded or called.')
        self.assertFalse(result['author_review']['real_model_eval_tested'])


class EvalCollectionTests(unittest.TestCase):
    def setUp(self):
        self.profile=p.load_profile()
        self.profile['id']='synthetic-collector-fixture'
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output=Path(self.temporary.name)/'new-review'

    def test_five_real_http_exchanges_have_no_inherited_answers_or_scores(self):
        with harness.mock_endpoint() as (url,requests):
            packet=p.collect_eval(self.profile,self.output,url,True)
        self.assertEqual(len(packet['runs']),5)
        self.assertEqual(len(requests),10)
        self.assertTrue(packet['collection_complete'])
        self.assertTrue(packet['all_runs_answered'])
        self.assertEqual(packet['quality'],'PENDING_MANUAL_REVIEW')
        for row in packet['runs']:
            self.assertTrue(all(value is None for value in row['assessment'].values()))
            self.assertNotIn('candidate',row)
            self.assertNotIn('samples',row)
            self.assertEqual(len(row['raw_replies']),2)
        self.assertIn('нет подготовленного объяснения',packet['runs'][4]['result']['answer'])
        self.assertNotIn('accepted_for_next_trial',packet)

    def test_repeated_trials_start_with_fresh_histories(self):
        with harness.mock_endpoint() as (url,requests):
            packet=p.collect_eval(self.profile,self.output,url,True,repeat=2)
        self.assertEqual(len(packet['runs']),10)
        self.assertEqual(len(requests),20)
        self.assertEqual(len(requests[10]['messages']),2)
        self.assertEqual(packet['runs'][5]['repeat'],2)

    def test_missing_optin_makes_no_request_or_directory(self):
        with harness.mock_endpoint() as (url,requests):
            with self.assertRaisesRegex(p.ProfileError,'upload'):
                p.collect_eval(self.profile,self.output,url)
            self.assertEqual(requests,[])
        self.assertFalse(self.output.exists())

    def test_existing_directory_and_symlink_are_not_overwritten(self):
        self.output.mkdir()
        marker=self.output/'keep'
        marker.write_text('existing result')
        with self.assertRaises(FileExistsError):
            p.collect_eval(self.profile,self.output,'http://127.0.0.1:8080/v1',True)
        self.assertEqual(marker.read_text(),'existing result')
        link=self.output.parent/'link'
        link.symlink_to(self.output,target_is_directory=True)
        with self.assertRaises(FileExistsError):
            p.collect_eval(self.profile,link,'http://127.0.0.1:8080/v1',True)

    def test_permanent_http_error_aborts_remaining_cases_without_retry(self):
        with harness.mock_endpoint(http_status=401) as (url,requests):
            packet=p.collect_eval(self.profile,self.output,url,True)
        self.assertEqual(len(requests),1)
        self.assertEqual(len(packet['runs']),1)
        self.assertEqual(len(packet['not_run']),4)
        self.assertFalse(packet['collection_complete'])
        self.assertEqual(packet['runs'][0]['result']['status'],'transport_error')

    def test_blocked_model_proposals_remain_ungraded(self):
        with harness.mock_endpoint(lambda _:harness.tool_reply('../secret')) as (url,requests):
            packet=p.collect_eval(self.profile,self.output,url,True)
        self.assertEqual(len(requests),5)
        self.assertTrue(packet['collection_complete'])
        self.assertFalse(packet['all_runs_answered'])
        self.assertTrue(all(row['result']['status']=='blocked' for row in packet['runs']))
        self.assertTrue(all(row['assessment']['conduct'] is None for row in packet['runs']))

    def test_invalid_endpoint_and_repeat_are_rejected_before_output(self):
        for repeat in (0,4,True):
            with self.subTest(repeat=repeat),self.assertRaises(p.ProfileError):
                p.collect_eval(self.profile,self.output,'http://127.0.0.1:8080/v1',True,repeat)
        with self.assertRaises(ValueError):
            p.collect_eval(self.profile,self.output,'http://user:secret@localhost/v1',True)
        self.assertFalse(self.output.exists())


if __name__=='__main__':
    unittest.main()
