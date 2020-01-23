"""클러스터 모듈."""
import os
from os.path import expanduser
import json
import datetime
import warnings
import time
import webbrowser
from urllib.request import urlopen
from urllib.error import URLError

import botocore
import boto3
import paramiko

from bilbo.profile import read_profile, DaskProfile, Profile
from bilbo.util import critical, warning, error, clust_dir, iter_clusters, \
    info, get_aws_config

warnings.filterwarnings("ignore")

NB_WORKDIR = "~/works"
TRY_SLEEP = 6


def cluster_info_exists(clname):
    """클러스터 정보가 존재하는가?"""
    path = os.path.join(clust_dir, clname + '.json')
    return os.path.isfile(path)


def _build_tag_spec(name, desc, _tags):
    tags = [{'Key': 'Name', 'Value': name}]
    if desc is not None:
        tags.append({'Key': 'Description', 'Value': desc})

    if _tags is not None:
        for _tag in _tags:
            tag = dict(Key=_tag[0], Value=_tag[1])
            tags.append(tag)

    tag_spec = [
        {
            'ResourceType': 'instance',
            'Tags': tags
        }
    ]
    return tag_spec


def create_ec2_instances(ec2, inst, cnt, tag_spec):
    """EC2 인스턴스 생성."""
    try:
        ins = ec2.create_instances(ImageId=inst.ami,
                                   InstanceType=inst.ec2type,
                                   MinCount=cnt, MaxCount=cnt,
                                   KeyName=inst.keyname,
                                   SecurityGroupIds=[inst.secgroup],
                                   TagSpecifications=tag_spec)
        return ins
    except botocore.exceptions.ClientError as e:
        if 'Request would have succeeded' not in str(e):
            raise e


def get_type_instance_info(pobj, only_inst=None):
    """인스턴스 종류별 공통 정보."""
    info = {}
    info['image_id'] = pobj.ami
    info['key_name'] = pobj.keyname
    info['ssh_user'] = pobj.ssh_user
    info['ssh_private_key'] = pobj.ssh_private_key
    info['ec2type'] = pobj.ec2type

    if only_inst is not None:
        info['instance_id'] = only_inst.instance_id
        info['public_ip'] = only_inst.public_ip_address
        info['private_dns_name'] = only_inst.private_dns_name
        if only_inst.tags is not None:
            info['tags'] = only_inst.tags
    return info


def create_dask_cluster(clname, pobj, ec2, clinfo):
    """Dask 클러스터 생성.

    Args:
        clname (str): 클러스터 이름. 이미 존재하면 에러
        pobj (bilbo.profile.Profile): 프로파일 정보
        ec2 (botocore.client.EC2): boto EC2 client
    """
    critical("Create dask cluster '{}'.".format(clname))

    # 기존 클러스터가 있으면 에러
    if cluster_info_exists(clname):
        raise Exception("Cluster '{}' already exists.".format(clname))

    clinfo['type'] = 'dask'

    # create scheduler
    scd_name = pobj.scd_inst.get_name(clname)
    scd_tag_spec = _build_tag_spec(scd_name, pobj.desc, pobj.scd_inst.tags)
    ins = create_ec2_instances(ec2, pobj.scd_inst, 1, scd_tag_spec)
    scd = ins[0]
    clinfo['instances'].append(scd.instance_id)
    clinfo['launch_time'] = datetime.datetime.now()

    # create workers
    wrk_name = pobj.wrk_inst.get_name(clname)
    wrk_tag_spec = _build_tag_spec(wrk_name, pobj.desc, pobj.wrk_inst.tags)
    ins = create_ec2_instances(ec2, pobj.wrk_inst, pobj.wrk_cnt, wrk_tag_spec)
    inst = pobj.wrk_inst
    winfo = get_type_instance_info(inst)
    winfo['count'] = pobj.wrk_cnt
    # 프로파일에서 지정된 thread/proc 수
    winfo['nthread'] = pobj.wrk_nthread
    winfo['nproc'] = pobj.wrk_nproc
    winfo['instances'] = []
    clinfo['worker'] = winfo
    for wrk in ins:
        clinfo['instances'].append(wrk.instance_id)

    # 사용 가능 상태까지 기다린 후 추가 정보 얻기.
    info("Wait for instance to be running.")
    scd.wait_until_running()
    scd.load()

    inst = pobj.scd_inst
    sinfo = get_type_instance_info(inst, scd)
    clinfo['scheduler'] = sinfo

    for wrk in ins:
        wrk.wait_until_running()
        wrk.load()
        wi = {}
        wi['instance_id'] = wrk.instance_id
        wi['public_ip'] = wrk.public_ip_address
        wi['private_dns_name'] = wrk.private_dns_name
        winfo['instances'].append(wi)

    # ec2 생성 후 반환값의 `ncpu_options` 가 잘못오고 있어 여기서 요청.
    if len(ins) > 0:
        winfo['cpu_info'] = get_cpu_info(pobj, ins[0])


def get_cpu_info(pobj, ins):
    """생성된 인스턴스에서 lscpu 명령으로 CPU 정보 얻기."""
    info("get_cpu_info")
    public_ip = ins.public_ip_address
    user = pobj.wrk_inst.ssh_user
    private_key = pobj.wrk_inst.ssh_private_key
    # Cores
    cmd = "lscpu | grep -e ^CPU\(s\): | awk '{print $2}'"
    res, _ = send_instance_cmd(user, private_key, public_ip, cmd)
    num_core = int(res[0])
    # Threads per core
    cmd = "lscpu | grep Thread | awk '{print $4}'"
    res, _ = send_instance_cmd(user, private_key, public_ip, cmd)
    threads_per_core = int(res[0])
    cpu_info = {'CoreCount': num_core, 'ThreadsPerCore': threads_per_core}
    return cpu_info


def save_cluster_info(clname, clinfo):
    """클러스터 정보파일 쓰기."""
    def json_default(value):
        if isinstance(value, datetime.date):
            return value.strftime('%Y-%m-%d %H:%M:%S')
        raise TypeError('not JSON serializable')

    warning("save_cluster_info: '{}'".format(clname))
    clinfo['ready_time'] = datetime.datetime.now()

    path = os.path.join(clust_dir, clname + '.json')
    with open(path, 'wt') as f:
        body = json.dumps(clinfo, default=json_default, indent=4,
                          sort_keys=True)
        f.write(body)


def load_cluster_info(clname):
    """클러스터 정보파일 읽기."""
    warning("load_cluster_info: '{}'".format(clname))
    path = os.path.join(clust_dir, clname + '.json')
    with open(path, 'rt') as f:
        body = f.read()
        clinfo = json.loads(body)
    return clinfo


def wait_until_connect(url, retry_count=10):
    """URL 접속이 가능할 때까지 기다림."""
    info("wait_until_connect: {}".format(url))
    for i in range(retry_count):
        try:
            urlopen(url, timeout=5)
            return
        except URLError:
            info("Can not connect to dashboard. Wait for a while.")
            time.sleep(TRY_SLEEP)
    raise ConnectionError()


def create_notebook(clname, pobj, ec2, clinfo):
    """노트북 생성."""
    critical("Create notebook.")
    nb_name = pobj.nb_inst.get_name(clname)
    nb_tag_spec = _build_tag_spec(nb_name, pobj.desc, pobj.nb_inst.tags)
    ins = ec2.create_instances(ImageId=pobj.nb_inst.ami,
                               InstanceType=pobj.nb_inst.ec2type,
                               MinCount=1, MaxCount=1,
                               KeyName=pobj.nb_inst.keyname,
                               SecurityGroupIds=[pobj.nb_inst.secgroup],
                               TagSpecifications=nb_tag_spec)

    nb = ins[0]
    info("Wait for notebook instance to be running.")
    nb.wait_until_running()
    nb.load()
    clinfo['instances'].append(nb.instance_id)
    ninfo = get_type_instance_info(pobj.nb_inst, nb)
    clinfo['notebook'] = ninfo


def check_dup_cluster(clname):
    """클러스터 이름이 겹치는지 검사."""
    path = os.path.join(clust_dir, clname + '.json')
    if os.path.isfile(path):
        raise NameError("Cluster '{}' already exist.".format(clname))


def create_cluster(profile, clname):
    """클러스터 생성."""

    if clname is None:
        clname = '.'.join(profile.lower().split('.')[0:-1])

    check_dup_cluster(clname)

    pcfg = read_profile(profile)
    ec2 = boto3.resource('ec2')

    # 클러스터 생성
    clinfo = {'name': clname, 'instances': []}
    if 'dask' in pcfg:
        pobj = DaskProfile(pcfg)
        pobj.validate()
        create_dask_cluster(clname, pobj, ec2, clinfo)
    else:
        pobj = Profile(pcfg)
        pobj.validate()

    if 'webbrowser' in pcfg:
        clinfo['webbrowser'] = pcfg['webbrowser']

    # 노트북 생성
    if 'notebook' in pcfg:
        create_notebook(clname, pobj, ec2, clinfo)

    return pobj, clinfo


def show_all_cluster():
    """모든 클러스터를 표시."""
    for cl in iter_clusters():
        name = '.'.join(cl.split('.')[0:-1])
        print("{}".format(name))


def check_cluster(clname):
    """프로파일을 확인.

    Args:
        clname (str): 클러스터명 (.json 확장자 제외)
    """
    if clname.lower().endswith('.json'):
        rname = '.'.join(clname.split('.')[0:-1])
        msg = "Wrong cluster name '{}'. Use '{}' instead.". \
              format(clname, rname)
        raise NameError(msg)

    # file existence
    path = os.path.join(clust_dir, clname + '.json')
    if not os.path.isfile(path):
        error("Cluster '{}' does not exist.".format(path))
        raise(FileNotFoundError(path))

    return path


def show_cluster(clname, detail=False):
    """클러스터 정보를 표시."""
    path = check_cluster(clname)
    if detail:
        with open(path, 'rt') as f:
            body = f.read()
            print(body)
        return

    info = load_cluster_info(clname)

    print()
    print("Cluster Name: {}".format(info['name']))
    print("Ready Time: {}".format(info['ready_time']))

    idx = 1
    if 'notebook' in info:
        print()
        print("Notebook:")
        idx = show_instance(idx, info['notebook'])
        print()

    if 'type' in info:
        cltype = info['type']
        print("Cluster Type: {}".format(cltype))
        if cltype == 'dask':
            show_dask_cluster(idx, info)
        else:
            raise NotImplementedError()
    print()


def show_instance(idx, inst):
    print("  [{}] instance_id: {}, public_ip: {}".
          format(idx, inst['instance_id'], inst['public_ip']))
    return idx + 1


def show_dask_cluster(idx, info):
    """Dask 클러스터 표시."""
    print()
    print("Scheduler:")
    scd = info['scheduler']
    idx = show_instance(idx, scd)

    print()
    print("Workers:")
    winfo = info['worker']
    for wrk in winfo['instances']:
        idx = show_instance(idx, wrk)


def check_git_modified(clinfo):
    """로컬 git 저장소 변경 여부.

    Commit 되지 않거나, Push 되지 않은 내용이 있으면 경고

    Returns:
        bool: 변경이 없거나, 유저가 확인한 경우 True

    """
    public_ip = clinfo['notebook']['public_ip']
    user = clinfo['notebook']['ssh_user']
    private_key = clinfo['notebook']['ssh_private_key']
    git_dir = clinfo['git_cloned_dir']

    cmd = "cd {} && git status --porcelain | grep '^ M.*'".format(git_dir)
    uncmts, _, = send_instance_cmd(user, private_key, public_ip, cmd)
    uncmt_cnt = len(uncmts)

    cmd = "cd {} && git cherry -v".format(git_dir)
    unpushs, _, = send_instance_cmd(user, private_key, public_ip, cmd)
    unpush_cnt = len(unpushs)

    if uncmt_cnt > 0 or unpush_cnt > 0:
        print()
        print("There are {} uncommitted file(s) and {} unpushed commits(s)!".
              format(uncmt_cnt, unpush_cnt))

        if uncmt_cnt > 0:
            print()
            print("Uncommitted file(s)")
            print("-------------------")
            for f in uncmts:
                print(f.strip())

        if unpush_cnt > 0:
            print()
            print("Unpushed commit(s)")
            print("-------------------")
            for f in unpushs:
                print(f.strip())

        print()
        ans = ''
        while ans.lower() not in ('y', 'n'):
            ans = input("Are you sure to destroy this cluster? (y/n): ")
        return ans == 'y'

    return True


def destroy_cluster(clname):
    """클러스터 제거."""
    check_cluster(clname)
    info = load_cluster_info(clname)

    if 'git_cloned_dir' in info:
        if not check_git_modified(info):
            print("Canceled.")
            return

    critical("Destroy cluster '{}'.".format(clname))
    # 인스턴스 제거
    ec2 = boto3.client('ec2')
    instances = info['instances']
    if len(instances) > 0:
        ec2.terminate_instances(InstanceIds=info['instances'])

    # 클러스터 파일 제거
    path = os.path.join(clust_dir, clname + '.json')
    os.unlink(path)


def send_instance_cmd(ssh_user, ssh_private_key, public_ip, cmd,
                      show_error=True, retry_count=10):
    """인스턴스에 SSH 명령어 실행

    https://stackoverflow.com/questions/42645196/how-to-ssh-and-run-commands-in-ec2-using-boto3

    Args:
        ssh_user (str): SSH 유저
        ssh_private_key (str): SSH Private Key 경로
        public_ip (str): 대상 인스턴스의 IP
        cmd (list): 커맨드 문자열 리스트
        show_error (bool): 에러 메시지 출력 여부
        retry_count (int): 재시도 횟수

    Returns:
        tuple: send_command 함수의 결과 (stdout, stderr)
    """
    info('send_instance_cmd - user: {}, key: {}, ip {}, cmd {}'
         .format(ssh_user, ssh_private_key, public_ip, cmd))

    key_path = expanduser(ssh_private_key)

    key = paramiko.RSAKey.from_private_key_file(key_path)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connected = False
    for i in range(retry_count):
        try:
            client.connect(hostname=public_ip, username=ssh_user, pkey=key)
        except (paramiko.ssh_exception.NoValidConnectionsError, TimeoutError):
            warning("Connection failed to '{}'. Retry after a while.".
                    format(public_ip))
            time.sleep(TRY_SLEEP)
        else:
            connected = True
            break

    if not connected:
        error("Connection failed to '{}'".format(public_ip))
        return

    stdin, stdout, stderr = client.exec_command(cmd)
    stdouts = stdout.readlines()
    err = stderr.read()
    if show_error and len(err) > 0:
        error(err.decode('utf-8'))

    client.close()

    return stdouts, err


def find_cluster_instance_by_public_ip(cluster, public_ip):
    """Public IP로 클러스터 인스턴스 정보 찾기."""
    clpath = check_cluster(cluster)

    with open(clpath, 'rt') as f:
        body = f.read()
        clinfo = json.loads(body)

    if clinfo['type'] == 'dask':
        scd = clinfo['scheduler']
        if scd['public_ip'] == public_ip:
            return scd
        winfo = clinfo['worker']
        for wrk in winfo['instances']:
            if wrk['public_ip'] == public_ip:
                return wrk
    else:
        raise NotImplementedError()


def dask_worker_options(winfo, memory):
    """Dask 클러스터 워커 인스턴스 정보에서 워커 옵션 구하기."""
    co = winfo['cpu_info']
    nproc = winfo['nproc'] or co['CoreCount']
    nthread = winfo['nthread'] or co['ThreadsPerCore']
    return nproc, nthread, memory // nproc


def start_cluster(pobj, clinfo):
    """클러스터 노트북, 마스터 & 워커를 시작."""
    if 'notebook' in clinfo:
        start_notebook(pobj, clinfo)

    if 'type' in clinfo:
        if clinfo['type'] == 'dask':
            start_dask_cluster(clinfo)
        else:
            raise NotImplementedError()


def git_clone_cmd(gobj, workdir):
    """Git 클론 명령 구성"""
    warning("git clone: {}".format(gobj.repository))
    repo = gobj.repository
    protocol, address = repo.split('://')
    url = "{}://{}:{}@{}".format(protocol, gobj.user, gobj.password, address)
    cmd = "cd {} && git clone {}".format(workdir, url)
    return cmd


def setup_aws_creds(user, private_key, public_ip):
    """AWS 크레덴셜 설치."""
    cmds = [
        'mkdir -p ~/.aws',
        'cd ~/.aws',
        'echo [default] > credentials',
        'echo [default] > config'
    ]

    ak, sk, dr = get_aws_config()
    cmd = 'echo "aws_access_key_id = {}" >> credentials'.format(ak)
    cmds.append(cmd)
    cmd = 'echo "aws_secret_access_key = {}" >> credentials'.format(sk)
    cmds.append(cmd)
    cmd = 'echo "region = {}" >> config'.format(dr)
    cmds.append(cmd)

    cmd = '; '.join(cmds)
    send_instance_cmd(user, private_key, public_ip, cmd)


def start_notebook(pobj, clinfo, retry_count=10):
    """노트북 시작.

    Args:
        clinfo (dict): 클러스터 생성 정보
        retry_count (int): 접속 URL 얻기 재시도 수. 기본 10

    Raises:
        TimeoutError: 재시도 수가 넘을 때

    """
    critical("Start notebook.")

    ncfg = clinfo['notebook']
    user, private_key = ncfg['ssh_user'], ncfg['ssh_private_key']
    public_ip = ncfg['public_ip']

    # AWS 크레덴셜 설치
    setup_aws_creds(user, private_key, public_ip)

    # 작업 폴더
    nb_workdir = pobj.nb_workdir or NB_WORKDIR
    cmd = "mkdir -p {}".format(nb_workdir)
    send_instance_cmd(user, private_key, public_ip, cmd)

    # git 설정이 있으면 설정
    if pobj.nb_git is not None:
        setup_git(pobj, user, private_key, public_ip, nb_workdir, clinfo)

    # 클러스터 타입별 노트북 설정
    vars = ''
    if 'type' in clinfo:
        if clinfo['type'] == 'dask':
            # dask-labextension을 위한 대쉬보드 URL
            ip = clinfo['scheduler']['public_ip']
            cmd = "mkdir -p ~/.jupyter/lab/user-settings/dask-labextension; "
            cmd += 'echo \'{{ "defaultURL": "http://{}:8787" }}\' > ' \
                   '~/.jupyter/lab/user-settings/dask-labextension/' \
                   'plugin.jupyterlab-settings'.format(ip)
            send_instance_cmd(user, private_key, public_ip, cmd)
            # 스케쥴러 주소
            dns = clinfo['scheduler']['private_dns_name']
            vars = "DASK_SCHEDULER_ADDRESS=tcp://{}:8786".format(dns)
        else:
            raise NotImplementedError()

    # Jupyter 시작
    ncmd = "cd {} && {} jupyter lab --ip 0.0.0.0".format(nb_workdir, vars)
    cmd = "screen -S bilbo -d -m bash -c '{}'".format(ncmd)
    send_instance_cmd(user, private_key, public_ip, cmd)

    # 접속 URL 얻기
    cmd = "jupyter notebook list | awk '{print $1}'"
    for i in range(retry_count):
        stdouts, _ = send_instance_cmd(user, private_key, public_ip, cmd)
        # url을 얻었으면 기록
        if len(stdouts) > 1:
            url = stdouts[1].strip().replace('0.0.0.0', public_ip)
            clinfo['notebook_url'] = url
            return
        info("Can not fetch notebook list. Wait for a while.")
        time.sleep(TRY_SLEEP)
    raise TimeoutError("Can not get notebook url.")


def setup_git(pobj, user, private_key, public_ip, nb_workdir, clinfo):
    """Git 설정 및 클론."""
    # config
    cmd = "git config --global user.name '{}'; ".format(pobj.nb_git.user)
    cmd += "git config --global user.email '{}'".format(pobj.nb_git.email)
    send_instance_cmd(user, private_key, public_ip, cmd)

    # 클론 (작업 디렉토리에)
    cmd = git_clone_cmd(pobj.nb_git, nb_workdir)
    send_instance_cmd(user, private_key, public_ip, cmd, False)
    gcdir = pobj.nb_git.repository.split('/')[-1].replace('.git', '')
    clinfo['git_cloned_dir'] = "{}/{}".format(nb_workdir, gcdir)


def start_dask_cluster(clinfo):
    """Dask 클러스터 마스터/워커를 시작."""
    critical("Start dask scheduler & workers.")

    # 스케쥴러 시작
    scd = clinfo['scheduler']
    user, private_key = scd['ssh_user'], scd['ssh_private_key']
    public_ip = scd['public_ip']
    scd_dns = scd['private_dns_name']
    cmd = "screen -S bilbo -d -m dask-scheduler"
    send_instance_cmd(user, private_key, public_ip, cmd)

    # AWS 크레덴셜 설치
    setup_aws_creds(user, private_key, public_ip)

    winfo = clinfo['worker']
    # 워커 실행 옵션
    public_ip = winfo['instances'][0]['public_ip']
    info("  Get worker memory from '{}'".format(public_ip))
    cmd = "free -b | grep 'Mem:' | awk '{print $2}'"
    stdouts, _ = send_instance_cmd(user, private_key, public_ip, cmd)
    memory = int(stdouts[0])
    nproc, nthread, memory = dask_worker_options(winfo, memory)
    # 결정된 옵션 기록
    winfo['nproc'] = nproc
    winfo['nthread'] = nthread
    winfo['memory'] = memory

    # 모든 워커들에 대해
    user, private_key = winfo['ssh_user'], winfo['ssh_private_key']
    for wrk in winfo['instances']:
        public_ip = wrk['public_ip']
        # AWS 크레덴셜 설치
        setup_aws_creds(user, private_key, public_ip)

        # 워커 시작
        public_ip = wrk['public_ip']
        opts = "--nprocs {} --nthreads {} --memory-limit {}".\
            format(nproc, nthread, memory)
        cmd = "screen -S bilbo -d -m dask-worker {}:8786 {}".\
            format(scd_dns, opts)
        warning("  Worker options: {}".format(opts))
        send_instance_cmd(user, private_key, public_ip, cmd)

    # Dask 스케쥴러의 대쉬보드 기다림
    dash_url = 'http://{}:8787'.format(scd['public_ip'])
    clinfo['dask_dashboard_url'] = dash_url
    critical("Waiting for Dask dashboard ready.")
    wait_until_connect(dash_url)


def stop_cluster(clname):
    """클러스터 마스터/워커를 중지.

    Returns:
        dict: 클러스터 정보(재시작 용)
    """
    clpath = check_cluster(clname)

    with open(clpath, 'rt') as f:
        body = f.read()
        clinfo = json.loads(body)

    if clinfo['type'] == 'dask':
        critical("Stop dask scheduler & workers.")
        # 스케쥴러 중지
        scd = clinfo['scheduler']
        user, private_key = scd['ssh_user'], scd['ssh_private_key']
        public_ip = scd['public_ip']
        cmd = "screen -X -S 'bilbo' quit"
        send_instance_cmd(user, private_key, public_ip, cmd)

        for wrk in clinfo['worker']:
            # 워커 중지
            user, private_key = wrk['ssh_user'], wrk['ssh_private_key']
            public_ip = wrk['public_ip']
            cmd = "screen -X -S 'bilbo' quit"
            send_instance_cmd(user, private_key, public_ip, cmd)
    else:
        raise NotImplementedError()

    return clinfo


def open_url(url, cldata):
    """지정된 또는 기본 브라우저로 URL 열기."""
    info("open_url")
    wb = webbrowser
    if 'webbrowser' in cldata:
        path = cldata['webbrowser']
        info("  Using explicit web browser: {}".format(path))
        webbrowser.register('explicit', None,
                            webbrowser.BackgroundBrowser(path))
        wb = webbrowser.get('explicit')
    wb.open(url)


def open_dashboard(clname):
    """클러스터의 대쉬보드 열기."""
    check_cluster(clname)
    clinfo = load_cluster_info(clname)

    if clinfo['type'] == 'dask':
        scd = clinfo['scheduler']
        public_ip = scd['public_ip']
        url = "http://{}:8787".format(public_ip)

        open_url(url, clinfo)
    else:
        raise NotImplementedError()


def open_notebook(clname):
    """노트북 열기."""
    check_cluster(clname)
    clinfo = load_cluster_info(clname)

    if 'notebook_url' in clinfo:
        url = clinfo['notebook_url']
        open_url(url, clinfo)
    else:
        raise Exception("No notebook instance.")
