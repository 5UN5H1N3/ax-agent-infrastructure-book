"""Prepare a first model profile; never download weights or start a server.

Run from the book root: python3 -m examples.model_profile.profile
--plan prints operator commands. --check reads a trusted local delivery.
--collect-eval NEW_DIR --base-url URL --allow-source-upload explicitly sends
synthetic L02 cases to the selected endpoint and writes a NEW review packet.
This collector never copies the prepared L02 answers or their manual scores.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile
import time
import urllib.parse

from examples.ch20_inference_contract import strict_json
from examples.learning_harness import evals, harness

ROOT=Path(__file__).resolve().parents[2]
DEFAULT=Path(__file__).with_name('qwen3_06b_profile.json')
CODE_FILES={
    'examples/learning_harness/harness.py','examples/learning_harness/evals.py',
    'examples/learning_harness/cases.json','examples/ch20_inference_contract.py',
    'examples/ch32_batching_exercise.py',
}
SIDECARS={'LICENSE.gguf.txt','MODEL_CARD.md','source-config.json',
          'source-tokenizer_config.json','LICENSE.source.txt'}


class ProfileError(ValueError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def validate(profile):
    """Validate this narrow Qwen3/GGUF teaching format, not arbitrary models."""
    if type(profile['schema_version']) is not int or profile['schema_version']!=1 or not re.fullmatch(r'[a-z0-9-]{1,80}',profile['id']):
        raise ProfileError('unsupported profile schema or id')
    for name in ('artifact_revision','source_revision_reference'):
        if not re.fullmatch(r'[0-9a-f]{40}',profile[name]):
            raise ProfileError('repository revisions must be full SHAs')
    runtime=profile['runtime']
    if runtime['repository']!='https://github.com/ggml-org/llama.cpp.git':
        raise ProfileError('this plan uses the official llama.cpp repository only')
    if not re.fullmatch(r'[0-9a-f]{40}',runtime['commit']):
        raise ProfileError('runtime needs a full commit SHA')
    if (type(runtime['parallel_slots']) is not int or runtime['parallel_slots']!=1
            or runtime['backend']!='CPU only' or runtime['cache_k_type']!='f16'
            or runtime['cache_v_type']!='f16' or runtime['reasoning']!='off'
            or runtime['jinja'] is not True or runtime['host']!='127.0.0.1'):
        raise ProfileError('this first profile requires one loopback CPU slot and f16 KV')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',runtime['alias']):
        raise ProfileError('invalid served alias')
    metadata=profile['gguf_metadata']
    if (metadata['architecture']!='qwen3' or metadata['version']!=3
            or metadata['quantization']!='Q8_0' or metadata['file_type']!=7):
        raise ProfileError('this checker only describes the specified Qwen3 GGUF v3 profile')
    for number in ('layers','query_heads','kv_heads','head_dim','hidden_size','context_length'):
        if type(metadata[number]) is not int or metadata[number]<=0:
            raise ProfileError('positive architecture integers are required')
    if (type(runtime['context_tokens']) is not int or runtime['context_tokens']<=512
            or runtime['context_tokens']>metadata['context_length']):
        raise ProfileError('runtime context exceeds recorded artifact support')
    if type(runtime['port']) is not int or not 1<=runtime['port']<=65535:
        raise ProfileError('invalid loopback port')
    for key in ('threads','top_k','seed'):
        if type(runtime[key]) is not int or runtime[key]<0:
            raise ProfileError('nonnegative integer runtime options are required')
    if runtime['threads']==0:
        raise ProfileError('explicit positive CPU thread count is required')
    for key in ('temperature','top_p','min_p','presence_penalty'):
        number=runtime[key]
        if isinstance(number,bool) or not isinstance(number,(float,int)) or not math.isfinite(number):
            raise ProfileError('finite sampling parameters are required')
    if not 0<runtime['top_p']<=1 or not 0<=runtime['min_p']<=1 or runtime['temperature']<0:
        raise ProfileError('sampling parameter outside range')
    for name in ('embedded_template_sha256','reference_template_sha256'):
        if not re.fullmatch(r'[0-9a-f]{64}',metadata[name]):
            raise ProfileError('template hashes must be SHA256')
    names=set()
    for item in profile['files']:
        name=item['name']
        if (not isinstance(name,str) or Path(name).name!=name or name in ('.','..')
                or name in names or not re.fullmatch(r'[a-zA-Z0-9_.-]+',name)):
            raise ProfileError('delivery names must be unique plain filenames')
        names.add(name)
        if (type(item['bytes']) is not int or not 0<item['bytes']<2**31
                or not re.fullmatch(r'[0-9a-f]{64}',item['sha256'])):
            raise ProfileError('delivery needs exact size and SHA256')
        url=urllib.parse.urlsplit(item['url'])
        if (url.scheme!='https' or url.netloc!='huggingface.co' or url.query or url.fragment
                or not re.search(r'/resolve/[0-9a-f]{40}/',url.path)):
            raise ProfileError('delivery URLs must pin a public HTTPS Hugging Face revision')
        reference=name in ('source-config.json','source-tokenizer_config.json','LICENSE.source.txt')
        repository=profile['source_repository_reference'] if reference else profile['artifact_repository']
        revision=profile['source_revision_reference'] if reference else profile['artifact_revision']
        upstream={'source-config.json':'config.json','source-tokenizer_config.json':'tokenizer_config.json',
                  'LICENSE.source.txt':'LICENSE','LICENSE.gguf.txt':'LICENSE','MODEL_CARD.md':'README.md'}.get(name,name)
        if item['url']!=f'https://huggingface.co/{repository}/resolve/{revision}/{upstream}':
            raise ProfileError('file URL differs from its repository/revision in the passport')
    models=names-SIDECARS
    if len(models)!=1 or not next(iter(models)).endswith('.gguf') or not SIDECARS<=names:
        raise ProfileError('one GGUF and all five reference sidecars are required')
    config=profile['harness']
    if (config['model_calls']!=harness.LIMITS['model_calls']
            or config['file_reads']!=harness.LIMITS['file_reads']
            or config['max_output_tokens']!=harness.LIMITS['output_tokens']
            or set(config['source_sha256'])!=CODE_FILES):
        raise ProfileError('profile differs from the L01/L02 harness contract')
    if (not isinstance(config['seconds_per_case'],(float,int))
            or isinstance(config['seconds_per_case'],bool)
            or not math.isfinite(config['seconds_per_case'])
            or not 0<config['seconds_per_case']<=300):
        raise ProfileError('bounded finite case deadline is required')
    if type(profile['memory']['kv_element_bytes']) is not int or profile['memory']['kv_element_bytes']!=2:
        raise ProfileError('this calculation requires f16 K and V')


def load_profile(path=DEFAULT):
    with Path(path).open('rb') as source:
        raw=source.read(65537)
    if len(raw)>65536:
        raise ProfileError('profile is too large')
    try:
        profile=strict_json(raw.decode('utf-8'))
        validate(profile)
    except (ValueError,TypeError,KeyError) as error:
        raise ProfileError('invalid profile: '+str(error)) from error
    return profile


def model_file(profile):
    return next(item for item in profile['files'] if item['name'].endswith('.gguf'))


def memory_sheet(profile):
    metadata,runtime=profile['gguf_metadata'],profile['runtime']
    kv=(2*metadata['layers']*metadata['kv_heads']*metadata['head_dim']
        *runtime['context_tokens']*profile['memory']['kv_element_bytes'])
    size=model_file(profile)['bytes']
    return {'artifact_file_bytes':size,'ideal_f16_kv_bytes_one_slot':kv,
            'file_plus_ideal_kv_bytes':size+kv,
            'file_plus_ideal_kv_gib':round((size+kv)/2**30,4),
            'measured_peak_ram_bytes':None,'measured_ttft_seconds':None,
            'interpretation':'Arithmetic reference only; file size is not resident weights; buffers/OS/peaks are not included.'}


def check_code(profile):
    for name,expected in profile['harness']['source_sha256'].items():
        if digest((ROOT/name).read_bytes())!=expected:
            raise ProfileError('L01/L02 code or corpus changed: '+name)


def inspect_file(directory,item):
    """Trusted directory and parents, no concurrent modification; no sandbox."""
    fd=os.open(Path(directory)/item['name'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ProfileError('delivery must contain regular files: '+item['name'])
        if info.st_size!=item['bytes']:
            raise ProfileError('size differs: '+item['name'])
        first=source.read(8)
        if item['name'].endswith('.gguf') and first!=b'GGUF\x03\x00\x00\x00':
            raise ProfileError('expected little-endian GGUF v3, not Safetensors or an LFS pointer')
        checksum=hashlib.sha256(first)
        while block:=source.read(1024*1024):
            checksum.update(block)
    if checksum.hexdigest()!=item['sha256']:
        raise ProfileError('SHA256 differs: '+item['name'])
    return {'name':item['name'],'bytes':info.st_size,'sha256':checksum.hexdigest()}


def check_delivery(profile,directory):
    validate(profile)
    check_code(profile)
    root=Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ProfileError('use a trusted real delivery directory')
    checked=[inspect_file(root,item) for item in profile['files']]
    # Expected hashes bind these small reference files before their contents are used.
    reference=strict_json((root/'source-config.json').read_text())
    metadata=profile['gguf_metadata']
    for field,key in (('num_hidden_layers','layers'),('num_attention_heads','query_heads'),
                      ('num_key_value_heads','kv_heads'),('head_dim','head_dim'),
                      ('hidden_size','hidden_size'),('max_position_embeddings','context_length')):
        if reference[field]!=metadata[key]:
            raise ProfileError('reference architecture differs: '+field)
    tokenizer=strict_json((root/'source-tokenizer_config.json').read_text())
    if digest(tokenizer['chat_template'].encode())!=metadata['reference_template_sha256']:
        raise ProfileError('reference template differs')
    return {'profile_id':profile['id'],'profile_sha256':digest(json.dumps(profile,sort_keys=True).encode()),
            'checked_files':checked,'delivery':'VERIFIED_LOCAL_BYTES',
            'effective_template':'embedded template bound by the full GGUF SHA256',
            'embedded_template_sha256':metadata['embedded_template_sha256'],
            'reference_template_differs':metadata['reference_template_sha256']!=metadata['embedded_template_sha256'],
            'runtime_identity':'NOT_RECORDED','interface':'NOT_RUN','quality':'NOT_RUN','capacity':'NOT_RUN',
            'provenance_limit':profile['provenance_note'],'memory':memory_sheet(profile)}


def plan(profile):
    validate(profile)
    runtime=profile['runtime']
    directory='ax-first-model'
    binary='./llama-l03/build/bin/llama-server'
    download=['set -eu','mkdir '+directory]
    for item in profile['files']:
        download.append(shlex.join(['curl','--fail','--location','--proto','=https',
             '--proto-redir','=https','--output',directory+'/'+item['name'],item['url']]))
    build=['set -eu','git clone --no-checkout '+runtime['repository']+' llama-l03',
           'git -C llama-l03 switch --detach '+runtime['commit'],
           'cmake -S llama-l03 -B llama-l03/build -DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DGGML_METAL=OFF -DGGML_NATIVE=OFF',
           'cmake --build llama-l03/build --target llama-server --parallel 2',
           binary+' --version','sha256sum '+binary]
    server=[binary,'--model',directory+'/'+model_file(profile)['name'],
        '--alias',runtime['alias'],'--host',runtime['host'],'--port',str(runtime['port']),
        '--ctx-size',str(runtime['context_tokens']),'--parallel','1','--gpu-layers','0',
        '--threads',str(runtime['threads']),'--threads-batch',str(runtime['threads']),
        '--cache-type-k','f16','--cache-type-v','f16','--jinja','--reasoning','off',
        '--temp',str(runtime['temperature']),'--top-p',str(runtime['top_p']),
        '--top-k',str(runtime['top_k']),'--min-p',str(runtime['min_p']),
        '--presence-penalty',str(runtime['presence_penalty']),'--seed',str(runtime['seed'])]
    return {'download_commands_not_executed':download,'build_commands_not_executed':build,
            'server_command_not_executed':shlex.join(server),
            'delivery_check':'python3 -m examples.model_profile.profile --check '+directory,
            'reminder':'Record actual binary hash, --version, build options, startup handler, host RAM and logs. These commands are not a build or runtime attestation.'}


def collect_eval(profile,output,base_url,allow_upload=False,repeat=1):
    validate(profile)
    check_code(profile)
    if not allow_upload:
        raise ProfileError('explicit --allow-source-upload is required')
    if type(repeat) is not int or not 1<=repeat<=3:
        raise ProfileError('repeat must be 1..3')
    complete=harness.transport(base_url)
    target=Path(output)
    target.mkdir()  # Refuse an existing path or symlink; parent must be trusted.
    corpus=strict_json(evals.DATA.read_text())
    packet={'kind':'new endpoint transcripts; no prepared answers or scores copied',
        'profile':profile,'profile_sha256':digest(json.dumps(profile,sort_keys=True).encode()),
        'endpoint':base_url,'endpoint_identity':'NOT_ATTESTED: alias does not prove loaded artifact',
        'corpus_sha256':digest(evals.DATA.read_bytes()),'runs':[],'not_run':[],
        'sampling':'server startup defaults expected from profile; raw replies and reported usage retained',
        'quality':'PENDING_MANUAL_REVIEW','capacity':'NOT_MEASURED'}
    schedule=[(iteration,case) for iteration in range(1,repeat+1) for case in corpus['cases']]
    aborted=False
    for iteration,case in schedule:
        if aborted:
            packet['not_run'].append({'repeat':iteration,'case':case['id'],'reason':'prior transport/deadline stop'})
            continue
        replies=[]
        def record(request,remaining):
            reply=complete(request,remaining)
            replies.append(reply)
            return reply
        with tempfile.TemporaryDirectory(prefix='ax-profile-eval-') as directory:
            (Path(directory)/'batching.py').write_text(evals.SOURCES[case['source']],encoding='utf-8')
            start=time.monotonic()
            result=harness.run(directory,record,goal=case['goal'],model=profile['runtime']['alias'],
                               seconds=profile['harness']['seconds_per_case'])
            elapsed=time.monotonic()-start
        packet['runs'].append({'repeat':iteration,'case':case['id'],'goal':case['goal'],
            'required_meaning':case['required'],'allowed_actions':case['allowed'],
            'result':result,'raw_replies':replies,'client_run_seconds':round(elapsed,6),
            'reported_usage':[reply.get('usage') if isinstance(reply,dict) else None for reply in replies],
            'assessment':{'quality':None,'faithfulness':None,'conduct':None,'reason':None}})
        aborted=result['status'] in ('transport_error','io_error','deadline')
    packet['collection_complete']=not packet['not_run']
    packet['all_runs_answered']=bool(packet['runs']) and all(item['result']['status']=='answered' for item in packet['runs'])
    with (target/'review.json').open('x',encoding='utf-8') as destination:
        json.dump(packet,destination,ensure_ascii=False,indent=2)
        destination.write('\n')
    return packet


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',type=Path,default=DEFAULT)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--plan',action='store_true')
    mode.add_argument('--check',type=Path,metavar='DELIVERY_DIR')
    mode.add_argument('--collect-eval',type=Path,metavar='NEW_REVIEW_DIR')
    parser.add_argument('--base-url')
    parser.add_argument('--allow-source-upload',action='store_true')
    parser.add_argument('--repeat',type=int,default=1)
    args=parser.parse_args()
    if args.collect_eval and not args.base_url:
        parser.error('--collect-eval requires --base-url')
    if args.base_url and not args.collect_eval:
        parser.error('--base-url is only used with --collect-eval')
    try:
        profile=load_profile(args.profile)
        if args.collect_eval:
            packet=collect_eval(profile,args.collect_eval,args.base_url,args.allow_source_upload,args.repeat)
            print(json.dumps({'review_file':str(args.collect_eval/'review.json'),
                'collection_complete':packet['collection_complete'],'all_runs_answered':packet['all_runs_answered'],
                'quality':'PENDING_MANUAL_REVIEW'},ensure_ascii=False,indent=2))
            return 0 if packet['collection_complete'] and packet['all_runs_answered'] else 2
        result=check_delivery(profile,args.check) if args.check else plan(profile) if args.plan else {
            'profile_id':profile['id'],'admission':profile['admission'],'memory':memory_sheet(profile),
            'author_review':profile['author_review'],'note':'No model downloaded, loaded or called.'}
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ProfileError,OSError,KeyError,TypeError,ValueError) as error:
        parser.exit(2,'Profile check stopped: '+str(error)+'\n')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
