"""Reproducible source/installer archives, using only the public file allowlist."""
import ast
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='1.1.0'
FORBIDDEN_NAMES={'clawbot_account.json','clawbot_state.json','chat_history.json','user_memory.json',
                 'persona.txt','search_cache.json','deployment.json','.xiaoa-install.json','wechat_login_qr.png'}
FORBIDDEN_PARTS={'data','assistant_data','runtime','setup-cache','__pycache__','.git','个人资料库'}


def build(root=ROOT):
    root=Path(root).resolve()
    names=json.loads((root/'release-files.json').read_text(encoding='utf-8'))
    if not isinstance(names,list) or not names or len(names)!=len(set(names)):
        raise ValueError('Invalid release file allowlist')
    for name in names:
        if not isinstance(name,str):raise ValueError('Invalid release path')
        relative=Path(name)
        path=(root/relative).resolve()
        if not path.is_relative_to(root) or relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Release path leaves the source directory')
        if (root/relative).is_symlink() or not path.is_file():raise ValueError('Missing or linked release file: '+name)
        if relative.name in FORBIDDEN_NAMES or set(relative.parts)&FORBIDDEN_PARTS:
            raise ValueError('Private/runtime file rejected: '+name)
        if relative.suffix.lower() in {'.sqlite3','.db','.log','.png','.jpg','.ico','.exe','.lnk'}:
            raise ValueError('Data/binary file rejected: '+name)
        source=path.read_text(encoding='utf-8-sig')
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|Bearer\s+[A-Za-z0-9._-]{20,}',source):
            raise ValueError('Possible secret in release file: '+name)
        if relative.suffix=='.py':ast.parse(source,filename=name)
    manifest={'product':'XiaoA Core','version':VERSION,'files':[
        {'path':name,'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest()} for name in names]}
    (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    output=root/'dist';output.mkdir(exist_ok=True)
    archives=[]
    for filename,prefix in [
            (f'小A_纯内核_一键安装包_v{VERSION}.zip',f'XiaoA-Core-{VERSION}'),
            (f'小A_GitHub源码_v{VERSION}.zip','xiaoa-core')]:
        archive=output/filename
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
            for name in names+['manifest.json']:
                info=zipfile.ZipInfo(prefix+'/'+name,date_time=(2026,10,28,0,0,0))
                info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16
                z.writestr(info,(root/name).read_bytes())
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise ValueError('Archive integrity check failed')
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        (output/(filename+'.sha256')).write_text(digest+'  '+filename+'\n',encoding='utf-8')
        archives.append({'name':filename,'bytes':archive.stat().st_size,'sha256':digest})
    return {'version':VERSION,'file_count':len(names)+1,'archives':archives}


if __name__=='__main__':print(json.dumps(build(),ensure_ascii=True))
