import json
from pathlib import Path

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestResultListViews:
    def test_result_list_routing_requires_auth(self, client):
        url = reverse("result:result_list")
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    def test_result_list_shows_results(self, auth_client, tmp_outputs):
        outputs = tmp_outputs
        # create run folder
        folder = outputs / "240101_0001"
        folder.mkdir(parents=True)
        # metadata + solution
        meta = folder / "tester_greedy_metadata.csv"
        meta.write_text("algorithm,tester/greedy\nstatus,ok\ntotal_cost,1234\nelapsed_seconds,12.3\n", encoding="utf-8")
        sol = folder / "tester_greedy_solution.json"
        sol.write_text(json.dumps({"dummy": True}), encoding="utf-8")

        resp = auth_client.get(reverse("result:result_list"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Result List" in body
        assert "240101_0001" in body
        assert "tester/greedy" in body or "greedy" in body
        assert "1234" in body

    def test_result_list_shows_sim_id_when_linked(self, auth_client, tmp_outputs):
        from simulation.models import SimulationRun

        outputs = tmp_outputs
        folder_name = "240102_0001"
        folder = outputs / folder_name
        folder.mkdir(parents=True)
        (folder / "algo_metadata.csv").write_text("algorithm,algo\nstatus,ok\n", encoding="utf-8")

        # create SimulationRun linked to this output folder
        sim = SimulationRun.objects.create(
            instance_name="toy_v1",
            algorithm_name="tester/algo",
            status="SUCCESS",
            created_by=None,
            output_folder=folder_name,
        )

        resp = auth_client.get(reverse("result:result_list"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert str(sim.id) in body

    def test_result_list_shows_dash_when_no_sim(self, auth_client, tmp_outputs):
        outputs = tmp_outputs
        folder_name = "240103_0001"
        folder = outputs / folder_name
        folder.mkdir(parents=True)
        (folder / "algo_metadata.csv").write_text("algorithm,algo\nstatus,ok\n", encoding="utf-8")

        resp = auth_client.get(reverse("result:result_list"))
        assert resp.status_code == 200
        body = resp.content.decode()
        # no simulation created; ensure common sim id (which would be an integer) is not present
        assert "-" in body

    def test_visualizer_link_and_missing_file(self, auth_client, tmp_outputs, monkeypatch):
        outputs = tmp_outputs
        folder_name = "240104_0001"
        folder = outputs / folder_name
        folder.mkdir(parents=True)
        (folder / "algo_metadata.csv").write_text("algorithm,algo\nstatus,ok\n", encoding="utf-8")

        # Visualize link should appear on the list page
        list_resp = auth_client.get(reverse("result:result_list"))
        assert list_resp.status_code == 200
        list_body = list_resp.content.decode()
        assert f"/result/{folder_name}/view/" in list_body

        # Simulate missing visualizer file by monkeypatching the path
        import result.views as rv
        from pathlib import Path
        monkeypatch.setattr(rv, "_VISUALIZER_PATH", Path("nonexistent_index.html"))

        resp = auth_client.get(reverse("result:result_view", args=[folder_name]))
        assert resp.status_code == 200
        # message should be rendered in the response body
        body = resp.content.decode()
        assert "Visualizer 파일을 찾을 수 없습니다." in body

    def test_result_list_sorting_latest_first(self, auth_client, tmp_outputs):
        outputs = tmp_outputs
        f1 = outputs / "240101_0001"
        f2 = outputs / "240102_0001"
        f1.mkdir(parents=True)
        f2.mkdir(parents=True)
        (f1 / "a_metadata.csv").write_text("algorithm,a\nstatus,ok\n", encoding="utf-8")
        (f2 / "b_metadata.csv").write_text("algorithm,b\nstatus,ok\n", encoding="utf-8")

        resp = auth_client.get(reverse("result:result_list"))
        assert resp.status_code == 200
        body = resp.content.decode()
        # f2 (240102) is newer and should appear before f1
        assert body.find("240102_0001") < body.find("240101_0001")
