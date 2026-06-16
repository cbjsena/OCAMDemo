from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from simulation.models import SimulationRun


@pytest.mark.django_db
class TestSimulationListCreateScenarios:
    def test_sim_lst_dis_001(self, auth_client):
        # Scenario: SIM_LST_DIS_001
        response = auth_client.get(reverse("simulation:simulation_list"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Simulation List" in body
        assert "Simulation Menu" in body

    def test_sim_lst_dis_002(self, auth_client, simulation_factory):
        # Scenario: SIM_LST_DIS_002
        simulation_factory(instance_name="toy_v1", algorithm_name="yongs/only_virtual")
        simulation_factory(instance_name="toy_v2", algorithm_name="kim/mcf_v5")
        response = auth_client.get(reverse("simulation:simulation_list"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Instance" in body and "Algorithm" in body and "Progress" in body
        assert "toy_v1" in body and "toy_v2" in body

    def test_sim_lst_dis_003(self, auth_client):
        # Scenario: SIM_LST_DIS_003
        response = auth_client.get(reverse("simulation:simulation_list"))
        assert response.status_code == 200
        assert "No simulations found." in response.content.decode()

    def test_sim_lst_dis_004(self, auth_client, simulation_factory):
        # Scenario: SIM_LST_DIS_004
        simulation_factory(status="PENDING")
        simulation_factory(status="RUNNING")
        simulation_factory(status="SUCCESS")
        simulation_factory(status="FAILED")
        simulation_factory(status="CANCELED")
        response = auth_client.get(reverse("simulation:simulation_list"))
        body = response.content.decode()
        assert "bg-warning text-dark" in body
        assert "bg-primary" in body
        assert "bg-success" in body
        assert "bg-danger" in body
        assert "bg-secondary" in body

    def test_sim_lst_dis_005(self, auth_client, simulation_factory):
        # Scenario: SIM_LST_DIS_005
        simulation_factory(progress=45, status="RUNNING")
        response = auth_client.get(reverse("simulation:simulation_list"))
        body = response.content.decode()
        assert "45%" in body
        assert "width: 45%" in body

    def test_sim_lst_dis_006(self, auth_client, simulation_factory):
        # Scenario: SIM_LST_DIS_006
        older = simulation_factory(instance_name="old")
        newest = simulation_factory(instance_name="new")
        middle = simulation_factory(instance_name="mid")
        now = timezone.now()
        SimulationRun.objects.filter(pk=older.pk).update(created_at=now - timedelta(hours=2))
        SimulationRun.objects.filter(pk=middle.pk).update(created_at=now - timedelta(hours=1))
        SimulationRun.objects.filter(pk=newest.pk).update(created_at=now)

        response = auth_client.get(reverse("simulation:simulation_list"))
        sims = list(response.context["simulations"])
        assert sims[0].instance_name == "new"

    def test_sim_lst_dis_007(self, client):
        # Scenario: SIM_LST_DIS_007
        response = client.get(reverse("simulation:simulation_list"))
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_sim_crt_dis_001(self, auth_client, simulation_env):
        # Scenario: SIM_CRT_DIS_001
        response = auth_client.get(reverse("simulation:simulation_create"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Create Simulation" in body
        assert "instance_name" in body and "algorithm_name" in body and "description" in body

    def test_sim_crt_dis_002(self, auth_client, sample_instances):
        # Scenario: SIM_CRT_DIS_002
        response = auth_client.get(reverse("simulation:simulation_create"))
        body = response.content.decode()
        assert "toy_v1" in body
        assert "toy_v2" in body

    def test_sim_crt_dis_003(self, auth_client, sample_instances):
        # Scenario: SIM_CRT_DIS_003
        response = auth_client.get(
            reverse("simulation:simulation_create") + "?instance_name=toy_v1"
        )
        assert response.status_code == 200
        assert response.context["selected_instance_name"] == "toy_v1"

    def test_sim_crt_dis_004(self, auth_client, sample_instances):
        # Scenario: SIM_CRT_DIS_004
        response = auth_client.get(reverse("simulation:simulation_create") + "?instance_name=nope")
        assert response.status_code == 200
        assert response.context["selected_instance_name"] == ""

    def test_sim_crt_dis_005(self, auth_client, sample_instances, sample_algorithms):
        # Scenario: SIM_CRT_DIS_005
        response = auth_client.get(reverse("simulation:simulation_create"))
        body = response.content.decode()
        assert sample_algorithms["valid"] in body
        assert sample_algorithms["valid2"] in body
        assert sample_algorithms["invalid"] in body
        assert "invalid solver.py" in body

    def test_sim_crt_dis_006(
        self, auth_client, sample_instances, sample_algorithms, mock_task_delay
    ):
        # Scenario: SIM_CRT_DIS_006
        response = auth_client.post(
            reverse("simulation:simulation_create"),
            {"instance_name": "toy_v1", "algorithm_name": sample_algorithms["valid"]},
            follow=False,
        )
        assert response.status_code == 302
        assert response.url == reverse("simulation:simulation_monitoring")

        sim = SimulationRun.objects.latest("id")
        assert sim.instance_name == "toy_v1"
        assert sim.algorithm_name == sample_algorithms["valid"]
        assert sim.task_id == "task-test-001"

    def test_sim_crt_dis_007(self, auth_client, sample_instances):
        # Scenario: SIM_CRT_DIS_007
        before = SimulationRun.objects.count()
        response = auth_client.post(
            reverse("simulation:simulation_create"),
            {"instance_name": "toy_v1", "algorithm_name": ""},
            follow=False,
        )
        assert response.status_code == 302
        assert response.url == reverse("simulation:simulation_create")
        assert SimulationRun.objects.count() == before

    def test_sim_crt_dis_008(
        self, auth_client, sample_instances, sample_algorithms, mock_task_delay
    ):
        # Scenario: SIM_CRT_DIS_008
        response = auth_client.post(
            reverse("simulation:simulation_create"),
            {
                "instance_name": "toy_v1",
                "algorithm_name": sample_algorithms["valid"],
                "description": "Test run",
            },
            follow=False,
        )
        assert response.status_code == 302
        sim = SimulationRun.objects.latest("id")
        assert sim.description == "Test run"

    def test_sim_crt_dis_009(self, client):
        # Scenario: SIM_CRT_DIS_009
        response = client.get(reverse("simulation:simulation_create"))
        assert response.status_code == 302
        assert "/accounts/login/" in response.url
