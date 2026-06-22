from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from simulation.models import SimulationRun


@pytest.mark.django_db
class TestSimulationListScenarios:
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

@pytest.mark.django_db
class TestSimulationDeleteScenarios:
    def test_sim_lst_dis_008_filter_by_created_by(self, auth_client, user, simulation_factory, other_user):
        # Scenario: SIM_LST_DIS_008 Created By 필터 기능 테스트
        sim1 = simulation_factory(status="SUCCESS")  # auth_client의 user 생성
        sim2 = simulation_factory(created_by=other_user, status="SUCCESS")

        # 필터 없이: 모든 시뮬레이션 표시
        resp = auth_client.get(reverse("simulation:simulation_list"))
        sims = list(resp.context["simulations"])
        assert len(sims) == 2

        # auth_client의 user로 필터링
        resp = auth_client.get(reverse("simulation:simulation_list") + f"?created_by_id={user.id}")
        sims = list(resp.context["simulations"])
        assert len(sims) == 1
        assert sims[0].id == sim1.id

        # other_user로 필터링
        resp = auth_client.get(reverse("simulation:simulation_list") + f"?created_by_id={other_user.id}")
        sims = list(resp.context["simulations"])
        assert len(sims) == 1
        assert sims[0].id == sim2.id

    def test_sim_lst_dis_009_owner_delete(self, auth_client, simulation_factory):
        # Scenario: SIM_LST_DIS_009 작성자가 삭제 가능
        sim = simulation_factory(status="SUCCESS")
        resp = auth_client.post(reverse("simulation:simulation_delete", kwargs={"sim_id": sim.id}))
        assert resp.status_code == 302
        assert resp.url == reverse("simulation:simulation_list")
        assert not SimulationRun.objects.filter(pk=sim.id).exists()

    def test_sim_lst_dis_010_non_owner_cannot_delete(self, auth_client, simulation_factory, other_user):
        # Scenario: SIM_LST_DIS_010 타사용자는 삭제 불가
        sim = simulation_factory(created_by=other_user)
        resp = auth_client.post(reverse("simulation:simulation_delete", kwargs={"sim_id": sim.id}))
        assert resp.status_code == 302
        assert resp.url == reverse("simulation:simulation_list")
        assert SimulationRun.objects.filter(pk=sim.id).exists()

    def test_sim_lst_dis_011_admin_can_delete(self, client, admin_user, simulation_factory, other_user):
        # Scenario: SIM_LST_DIS_011 관리자 권한으로 다른 사용자가 만든 시뮬레이션 삭제 가능
        client.login(username="admin_user", password="password")
        sim = simulation_factory(created_by=other_user, status="SUCCESS")
        resp = client.post(reverse("simulation:simulation_delete", kwargs={"sim_id": sim.id}))
        assert resp.status_code == 302
        assert resp.url == reverse("simulation:simulation_list")
        assert not SimulationRun.objects.filter(pk=sim.id).exists()
