"""프로파일 테스트."""
import  pytest

from bilbo.profile import Profile, DaskProfile, override_cfg_by_params


def test_dask_basic():
    cfg = {
        "description": "Description",
        "instance": {
            'ami': 'ami-000',
            "ec2type": "base-ec2type",
            "keyname": "base-key",
            "ssh_user": "ubuntu",
            "ssh_private_key": "base-key.pem",
            "tags": [
                ["Owner", "BaseOwner"],
                ["Service", "BaseService"]
            ]
        },
        "dask": {}
    }
    pro = DaskProfile(cfg)
    assert pro.desc == 'Description'
    assert pro.inst.ami == 'ami-000'
    assert pro.inst.ec2type == 'base-ec2type'
    assert pro.inst.keyname == 'base-key'
    assert pro.inst.ssh_user == 'ubuntu'
    assert pro.inst.ssh_private_key == 'base-key.pem'
    assert pro.inst.secgroup is None
    assert pro.type == 'dask'
    assert len(pro.inst.tags) == 2
    assert len(pro.inst.tags[0]) == 2

    assert pro.nb_inst is None

    assert pro.scd_inst.ami == 'ami-000'
    assert pro.scd_inst.ec2type == 'base-ec2type'
    assert pro.scd_inst.keyname == 'base-key'
    assert pro.scd_inst.secgroup is None
    assert pro.scd_cnt == 1

    assert pro.wrk_inst.ami == 'ami-000'
    assert pro.wrk_inst.ec2type == 'base-ec2type'
    assert pro.wrk_inst.keyname == 'base-key'
    assert pro.wrk_inst.secgroup is None
    assert pro.wrk_cnt == 1


def test_dask_complex():
    cfg = {
        "instance": {
            'ami': 'ami-000',
            "ec2type": "base-ec2type",
            "security_group": "sg-000",
            "keyname": "base-key",
            "ssh_user": "ubuntu",
            "ssh_private_key": "~/.ssh/base-key.pem",
            "tags": [
                ["Owner", "BaseOwner"],
                ["Service", "BaseService"]
            ]
        },
        "dask": {
            'scheduler': {
                "instance": {
                    'ami': 'ami-001',
                    "ec2type": "scd-ec2type",
                    "security_group": "sg-001",
                    "keyname": "scd-key",
                    "ssh_user": "ec2-user",
                    "ssh_private_key": "scd-key.pem",
                    "tags": [
                        ["Owner", "ScdOwner"],
                        ["Service", "ScdService"]
                    ]
                }
            },
            'worker': {
                "instance": {
                    'ami': 'ami-002',
                    "ec2type": "wrk-ec2type",
                    "keyname": "wrk-key",
                    "ssh_private_key": "wrk-key.pem",
                    "tags": [
                        ["Owner", "WrkOwner"],
                        ["Service", "WrkService"]
                    ]
                },
                "count": 2
            }
        }
    }
    pro = DaskProfile(cfg)
    assert pro.inst.ami == 'ami-000'
    assert pro.inst.ec2type == 'base-ec2type'
    assert pro.inst.secgroup == 'sg-000'
    assert pro.clcfg is not None
    assert type(pro) is DaskProfile

    assert pro.scd_inst.ami == 'ami-001'
    assert pro.scd_inst.ec2type == 'scd-ec2type'
    assert pro.scd_inst.keyname == 'scd-key'
    assert pro.scd_inst.ssh_user == 'ec2-user'
    assert pro.scd_inst.ssh_private_key == 'scd-key.pem'
    assert pro.scd_inst.secgroup == 'sg-001'
    assert pro.scd_cnt == 1
    assert len(pro.scd_inst.tags) == 2
    assert pro.scd_inst.tags[0][1] == "ScdOwner"

    assert pro.wrk_inst.ami == 'ami-002'
    assert pro.wrk_inst.ec2type == 'wrk-ec2type'
    assert pro.wrk_inst.keyname == 'wrk-key'
    assert pro.wrk_inst.secgroup == 'sg-000'
    assert pro.wrk_inst.ssh_user == 'ubuntu'
    assert pro.wrk_inst.ssh_private_key == 'wrk-key.pem'
    assert pro.wrk_cnt == 2
    assert len(pro.wrk_inst.tags) == 2
    assert pro.wrk_inst.tags[0][1] == "WrkOwner"


def test_profile():
    cfg = {
    }
    pro = Profile(cfg)
    assert pro.inst is None
    assert pro.nb_inst is None

    cfg = {
        "notebook": {
        }
    }
    with pytest.raises(RuntimeError, match=r"No instance config.*notebook.*"):
        pro = Profile(cfg)

    cfg = {
        "notebook": {
            "instance": {}
        }
    }
    pro = Profile(cfg)
    with pytest.raises(RuntimeError, match=r"No 'ami'.*"):
        pro.validate()

    cfg = {
        "instance_prefix": "my-",
        "instance": {
            'ami': 'ami-000',
            "ec2type": "base-ec2type",
            "security_group": "sg-000",
            "keyname": "base-key",
            "ssh_user": "ubuntu",
            "ssh_private_key": "~/.ssh/base-key.pem",
            "tags": [
                ["Owner", "BaseOwner"],
                ["Service", "BaseService"]
            ]
        },
        "notebook": {
            "instance": {
                "ec2type": "m5.xlarge"
            },
            "workdir": "~/works",
            "git": {
                "repository": "https://github.com/haje01/bilbo.git",
                "user": "haje01",
                "password": "password",
                "email": "haje01@gmail.com"
            }
        }
    }
    pro = Profile(cfg)
    assert pro.inst_prefix == "my-"
    assert pro.nb_inst is not None
    assert pro.nb_inst.ami == 'ami-000'
    assert pro.nb_inst.ec2type == "m5.xlarge"
    assert pro.nb_workdir == '~/works'
    assert pro.nb_git is not None
    assert pro.nb_git.user == 'haje01'
    assert pro.nb_git.password == 'password'
    assert pro.nb_inst.get_name('test') == "my-test-notebook"


def test_validate():
    """프로파일 유효성 테스트."""
    cfg = {
        "instance": {
            'ami': 'ami-000',
            "ec2type": "base-ec2type",
            "security_group": "sg-000",
            "keyname": "base-key",
            "tags": [
                ["Owner", "BaseOwner"],
                ["Service", "BaseService"]
            ]
        },
        "notebook": {
            "instance": {
                "ec2type": "m5.xlarge"
            }
        }
    }
    pro = Profile(cfg)
    with pytest.raises(RuntimeError, match=r".*ssh.*"):
        pro.validate()

    cfg = {
        "instance": {
            'ami': 'ami-000',
            "ec2type": "base-ec2type",
            "security_group": "sg-000",
            "keyname": "base-key",
            "tags": [
                ["Owner", "BaseOwner"],
                ["Service", "BaseService"]
            ]
        },
        "dask": {
            "worker": {
                "count": 2
            }
        }
    }
    pro = DaskProfile(cfg)
    with pytest.raises(RuntimeError, match=r".*ssh.*"):
        pro.validate()


def test_params():
    """CLI 패러미터로 프로파일 덮어쓰기 테스트."""
    cfg = {
        "instance": {
            'ami': 'ami-000',
            "ec2type": "base-ec2type",
            "security_group": "sg-000",
            "keyname": "base-key",
            "tags": [
                ["Owner", "BaseOwner"],
                ["Service", "BaseService"]
            ]
        },
        "notebook": {
            "instance": {
                "ec2type": "m5.xlarge"
            }
        }
    }
    with pytest.raises(RuntimeError, match=r"Parameter syntax.*"):
        params = ['instance.ec2type param-ec2type']
        override_cfg_by_params(cfg, params)

    with pytest.raises(RuntimeError, match=r"Parameter syntax.*"):
        params = ['instance.ec2type = param-ec2type']
        override_cfg_by_params(cfg, params)

    with pytest.raises(RuntimeError, match=r"Illegal list index.*"):
        params = ['instance.tags.a.b=ParamOwner']
        override_cfg_by_params(cfg, params)

    params = ['instance.ec2type=param-ec2type',
              'instance.tags.0.1=ParamOwner',
              'dask.worker.count=2']
    override_cfg_by_params(cfg, params)
    assert cfg['instance']['ec2type'] == 'param-ec2type'
    assert cfg['instance']['tags'][0][1] == 'ParamOwner'
    assert cfg['dask']['worker']['count'] == 2

    with pytest.raises(IndexError):
        params = ['instance.tags.3.3=1']
        override_cfg_by_params(cfg, params)

    params = ['instance.3.3=1']
    override_cfg_by_params(cfg, params)
    import pdb; pdb.set_trace()
    pass