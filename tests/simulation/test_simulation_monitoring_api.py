import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestSimulationMonitoringApiScenarios:
    def test_sim_mon_dis_001(self, auth_client):
        # Scenario: SIM_MON_DIS_001
        response = auth_client.get(reverse("simulation:simulation_monitoring"))
        assert response.status_code == 200
        assert "Simulation Monitoring" in response.content.decode()

    def test_sim_mon_dis_002(self, auth_client, simulation_factory):
        # Scenario: SIM_MON_DIS_002
        simulation_factory(status="PENDING")
        simulation_factory(status="RUNNING")
        simulation_factory(status="SUCCESS")
        response = auth_client.get(reverse("simulation:simulation_monitoring"))
        sims = list(response.context["simulations"])
        assert {s.status for s in sims}.issubset({"PENDING", "RUNNING"})

    def test_sim_mon_dis_003(self, auth_client, simulation_factory):
        # Scenario: SIM_MON_DIS_003
        simulation_factory(status="SUCCESS")
        response = auth_client.get(reverse("simulation:simulation_monitoring"))
        assert "No running simulations." in response.content.decode()

    def test_sim_mon_dis_004(self, auth_client, simulation_factory):
        # Scenario: SIM_MON_DIS_004
        simulation_factory(status="RUNNING", progress=40, model_status="Solving")
        response = auth_client.get(reverse("simulation:simulation_monitoring"))
        body = response.content.decode()
        assert "40%" in body
        assert "Solving" in body

    def test_sim_mon_dis_005(self, auth_client, simulation_factory):
        # Scenario: SIM_MON_DIS_005
        sim = simulation_factory(status="RUNNING")
        response = auth_client.get(reverse("simulation:simulation_monitoring"))
        body = response.content.decode()
        assert "Cancel" in body
        assert reverse("simulation:simulation_cancel", kwargs={"sim_id": sim.id}) in body

    def test_sim_mon_dis_006(self, client):
        # Scenario: SIM_MON_DIS_006
        response = client.get(reverse("simulation:simulation_monitoring"))
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_sim_mon_api_001(self, auth_client, simulation_factory):
        # Scenario: SIM_MON_API_001
        sim = simulation_factory(status="RUNNING", progress=55, model_status="Step A")
        response = auth_client.get(
            reverse("simulation:simulation_status_api", kwargs={"sim_id": sim.id})
        )
        assert response.status_code == 200
        data = response.json()
        assert set(["id", "status", "progress", "model_status"]).issubset(data.keys())
        assert data["id"] == sim.id

    def test_sim_mon_api_002(self, auth_client):
        # Scenario: SIM_MON_API_002
        response = auth_client.get(
            reverse("simulation:simulation_status_api", kwargs={"sim_id": 999999})
        )
        assert response.status_code == 404

    def test_sim_mon_api_003(self, auth_client, simulation_factory):
        # Scenario: SIM_MON_API_003
        sim = simulation_factory(status="PENDING", model_status="queued")
        response = auth_client.post(
            reverse("simulation:simulation_cancel", kwargs={"sim_id": sim.id}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "CANCELED"
        sim.refresh_from_db()
        assert sim.status == "CANCELED"
        assert sim.model_status == "Canceled by user"

    def test_sim_mon_api_004(self, auth_client, simulation_factory):
        # Scenario: SIM_MON_API_004
        sim = simulation_factory(status="SUCCESS")
        response = auth_client.post(
            reverse("simulation:simulation_cancel", kwargs={"sim_id": sim.id}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "SUCCESS"
        sim.refresh_from_db()
        assert sim.status == "SUCCESS"

    def test_sim_mon_api_005(self, auth_client, simulation_factory):
        # Scenario: SIM_MON_API_005
        sim = simulation_factory(status="PENDING")
        response = auth_client.post(
            reverse("simulation:simulation_cancel", kwargs={"sim_id": sim.id})
        )
        assert response.status_code == 302
        assert response.url == reverse("simulation:simulation_monitoring")

    def test_sim_mon_api_006(self, client, simulation_factory):
        # Scenario: SIM_MON_API_006
        sim = simulation_factory(status="PENDING")
        status_resp = client.get(
            reverse("simulation:simulation_status_api", kwargs={"sim_id": sim.id})
        )
        cancel_resp = client.post(
            reverse("simulation:simulation_cancel", kwargs={"sim_id": sim.id})
        )
        assert status_resp.status_code == 302
        assert cancel_resp.status_code == 302
        assert "/accounts/login/" in status_resp.url
        assert "/accounts/login/" in cancel_resp.url
