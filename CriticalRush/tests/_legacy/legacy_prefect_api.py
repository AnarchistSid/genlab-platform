"""Tests for the Prefect REST API client."""



class TestPrefectRESTClient:
    def test_default_api_url(self):
        from genlab_core.orchestration.prefect_api import PrefectRESTClient
        client = PrefectRESTClient()
        assert client._api_url == "http://127.0.0.1:4200/api"

    def test_custom_api_url(self):
        from genlab_core.orchestration.prefect_api import PrefectRESTClient
        client = PrefectRESTClient("http://custom:9000/api")
        assert client._api_url == "http://custom:9000/api"

    def test_custom_api_url_strips_trailing_slash(self):
        from genlab_core.orchestration.prefect_api import PrefectRESTClient
        client = PrefectRESTClient("http://custom:9000/api/")
        assert client._api_url == "http://custom:9000/api"

    def test_healthy_returns_false_on_connection_error(self):
        from genlab_core.orchestration.prefect_api import PrefectRESTClient
        client = PrefectRESTClient("http://localhost:99999/api")
        assert client.healthy() is False

    def test_list_flow_runs_returns_empty_on_error(self):
        from genlab_core.orchestration.prefect_api import PrefectRESTClient
        client = PrefectRESTClient("http://localhost:99999/api")
        result = client.list_flow_runs(limit=5)
        assert result == []

    def test_get_flow_run_returns_none_on_error(self):
        from genlab_core.orchestration.prefect_api import PrefectRESTClient
        client = PrefectRESTClient("http://localhost:99999/api")
        result = client.get_flow_run("nonexistent-id")
        assert result is None

    def test_trigger_deployment_returns_none_without_server(self):
        from genlab_core.orchestration.prefect_api import PrefectRESTClient
        client = PrefectRESTClient("http://localhost:99999/api")
        result = client.trigger_deployment("test/test")
        assert result is None


class TestDeploymentManager:
    def test_flow_registry_has_entries(self):
        from genlab_core.orchestration.deployment_manager import _FLOW_REGISTRY
        assert len(_FLOW_REGISTRY) >= 3  # ai_creators, gaming, spike_detector

    def test_registry_structure(self):
        from genlab_core.orchestration.deployment_manager import _FLOW_REGISTRY
        for module_path, func_name, dep_configs in _FLOW_REGISTRY:
            assert isinstance(module_path, str)
            assert isinstance(func_name, str)
            assert isinstance(dep_configs, list)
            for cfg in dep_configs:
                assert "name" in cfg
                assert "tags" in cfg
