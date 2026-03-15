from yuubot.core.media_paths import MediaPathContext, MediaPathError, host_to_runtime, runtime_to_host


def test_host_to_runtime_maps_host_path_inside_docker():
    ctx = MediaPathContext(
        docker_host_mount="/mnt/host",
        host_home_dir="/home/tester",
        container_home_dir="/root",
    )

    assert host_to_runtime("/home/tester/pic.png", ctx=ctx) == "/mnt/host/home/tester/pic.png"


def test_runtime_to_host_restores_shared_mount_path():
    ctx = MediaPathContext(
        docker_host_mount="/mnt/host",
        host_home_dir="/home/tester",
        container_home_dir="/root",
    )

    assert runtime_to_host("/mnt/host/home/tester/pic.png", ctx=ctx) == "/home/tester/pic.png"


def test_runtime_to_host_maps_container_home_back_to_host_home():
    ctx = MediaPathContext(
        docker_host_mount="/mnt/host",
        host_home_dir="/home/tester",
        container_home_dir="/root",
    )

    assert runtime_to_host("/root/generated/out.png", ctx=ctx) == "/home/tester/generated/out.png"


def test_runtime_to_host_rejects_non_shared_container_path():
    ctx = MediaPathContext(
        docker_host_mount="/mnt/host",
        host_home_dir="/home/tester",
        container_home_dir="/root",
    )

    try:
        runtime_to_host("/var/tmp/out.png", ctx=ctx)
    except MediaPathError as exc:
        assert "共享目录 ~/" in str(exc)
    else:
        raise AssertionError("expected MediaPathError")


def test_runtime_to_host_without_docker_allows_plain_host_paths():
    ctx = MediaPathContext(
        docker_host_mount="",
        host_home_dir="",
        container_home_dir="",
    )

    assert runtime_to_host("/tmp/out.png", ctx=ctx) == "/tmp/out.png"
