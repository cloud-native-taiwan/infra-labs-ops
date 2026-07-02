from account_automation.models import DeletePreview, ResourceItem


def test_delete_preview_empty_project() -> None:
    preview = DeletePreview(username="alice", user_found=True, project_found=False)

    assert preview.servers == ()
    assert preview.volumes == ()
    assert preview.networks == ()
    assert preview.floating_ips == ()
    assert preview.load_balancers == ()


def test_delete_preview_with_resources() -> None:
    preview = DeletePreview(
        username="alice",
        user_found=True,
        project_found=True,
        servers=(ResourceItem(id="s1", name="web", extra="ACTIVE"),),
        volumes=(
            ResourceItem(id="v1", name="data", extra="in-use"),
            ResourceItem(id="v2", name="backup", extra="available"),
        ),
    )

    assert len(preview.servers) == 1
    assert preview.servers[0].name == "web"
    assert len(preview.volumes) == 2
