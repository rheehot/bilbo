import os
import warnings

import pytest

from bilbo.util import prof_dir
from bilbo.cluster import create_cluster, destroy_cluster, start_cluster, \
    save_cluster_info

warnings.filterwarnings("ignore")

PRO_NAME = '_bilbotest_.json'

@pytest.fixture(scope="function")
def cluster():
    """프로파일 픽스쳐."""
    propath = os.path.join(prof_dir, PRO_NAME)
    clname = PRO_NAME.split('.')[0]
    f = open(propath, 'wt')

    def _create(body):
        f.write(body)
        f.close()
        clinfo = create_cluster(PRO_NAME, clname)
        start_cluster(clinfo)
        save_cluster_info(clname, clinfo)
        return clinfo

    yield _create

    destroy_cluster(clname)
    os.unlink(propath)


def test_empty(cluster):
    clinfo = cluster("""
    {
    "$schema": "https://raw.githubusercontent.com/haje01/bilbo/master/schemas/profile-01.schema.json",
    "instance": {
        "ami": "ami-0f49fa254e1806b72",
        "ec2type": "t3.micro",
        "security_group": "sg-0bc538e0a7c089b4d",
        "keyname": "wzdat-seoul",
        "ssh_user": "ubuntu",
        "ssh_private_key": "~/.ssh/my-private.pem"
    }
}""")
    assert len(clinfo['instances']) == 0


def test_notebook_only(cluster):
    """노트북만 테스트."""
    clinfo = cluster("""
{
    "$schema": "file:///Users/haje01/works/bilbo/schemas/profile-01.schema.json",
    "instance": {
        "ami": "ami-0f49fa254e1806b72",
        "ec2type": "t3.micro",
        "keyname": "wzdat-seoul",
        "security_group": "sg-0bc538e0a7c089b4d",
        "ssh_user": "ubuntu",
        "ssh_private_key": "~/.ssh/wzdat-seoul.pem"
    },
    "notebook": {
        "instance": {
            "ec2type": "t3.nano"
        }
    }
}
    """)
    assert 'type' not in clinfo
    assert 'notebook_url' in clinfo
    assert 'token' in clinfo['notebook_url']
    assert 'notebook' in clinfo
    ninfo = clinfo['notebook']
    assert ninfo['ec2type'] == 't3.nano'


def test_dask(cluster):
    """Dask 클러스터 테스트."""
    clinfo = cluster("""
{
    "$schema": "file:///Users/haje01/works/bilbo/schemas/profile-01.schema.json",
    "instance": {
        "ami": "ami-0f49fa254e1806b72",
        "ec2type": "t3.micro",
        "keyname": "wzdat-seoul",
        "security_group": "sg-0bc538e0a7c089b4d",
        "ssh_user": "ubuntu",
        "ssh_private_key": "~/.ssh/wzdat-seoul.pem",
        "tags": [
            ["Owner", "haje01@gmail.com"]
        ]
    },
    "dask": {
        "worker": {
            "instance": {
                "ec2type": "t3.micro"
            },
            "nthread": 2,
            "count": 2
        }
    }
}
    """)
    assert clinfo['type'] == 'dask'
    assert clinfo['name'] == PRO_NAME.split('.')[0]

    assert 'notebook' not in clinfo

    assert 'scheduler' in clinfo
    sinfo = clinfo['scheduler']
    assert 'public_ip' in sinfo
    assert 'private_dns_name' in sinfo
    assert len(sinfo['tags']) == 2

    assert 'worker' in clinfo
    winfo = clinfo['worker']
    assert winfo['nthread'] == 2
    assert winfo['nproc'] == winfo['cpu_info']['CoreCount']
    wins = winfo['instances']
    assert len(wins) == 2
    wins = winfo['instances']
    assert 'public_ip' in wins[0]
    assert 'private_dns_name' in wins[0]
