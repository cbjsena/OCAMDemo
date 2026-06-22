import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse


@pytest.mark.django_db
class TestSimulationAlgorithmUploadScenarios:
    def test_sim_alg_dis_001(self, auth_client, simulation_env):
        # Scenario: SIM_ALG_DIS_001
        response = auth_client.get(reverse("simulation:simulation_algorithm_upload"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Algorithm Upload" in body
        assert "simulation_algorithm_upload" in body or "Algorithm Upload" in body

    def test_sim_alg_dis_002(self, auth_client, simulation_env):
        # Scenario: SIM_ALG_DIS_002
        response = auth_client.get(reverse("simulation:simulation_algorithm_upload"))
        body = response.content.decode()
        assert "Required ZIP Structure" in body
        assert "Minimum solver.py Contract" in body

    def test_sim_alg_dis_003(self, auth_client, simulation_env):
        # Scenario: SIM_ALG_DIS_003
        response = auth_client.post(
            reverse("simulation:simulation_algorithm_upload"), {}, follow=True
        )
        assert response.status_code == 200
        msgs = [str(m) for m in response.context["messages"]]
        assert any("select a file" in m.lower() for m in msgs)

    def test_sim_alg_dis_004(self, auth_client, simulation_env):
        # Scenario: SIM_ALG_DIS_004
        txt = SimpleUploadedFile("invalid.txt", b"abc", content_type="text/plain")
        response = auth_client.post(
            reverse("simulation:simulation_algorithm_upload"),
            {"algorithm_zip": txt},
            follow=True,
        )
        assert response.status_code == 200
        msgs = [str(m) for m in response.context["messages"]]
        assert any(".zip" in m.lower() or "zip" in m.lower() for m in msgs)

    def test_sim_alg_dis_005(self, auth_client, simulation_env, algorithm_zip_factory):
        # Scenario: SIM_ALG_DIS_005
        zip_file = algorithm_zip_factory(
            "valid.zip",
            {
                "gildong/vessel_swap/__init__.py": b"",
                "gildong/vessel_swap/solver.py": b"def algorithm(instance_data, timelimit):\n"
                b"    return {'ok': True}\n",
            },
        )
        response = auth_client.post(
            reverse("simulation:simulation_algorithm_upload"),
            {"algorithm_zip": zip_file},
            follow=False,
        )
        assert response.status_code == 302
        assert response.url == reverse("simulation:simulation_create")
        assert (simulation_env["algorithms_dir"] / "gildong" / "vessel_swap" / "solver.py").exists()

    def test_sim_alg_dis_006(self, auth_client, simulation_env, algorithm_zip_factory):
        # Scenario: SIM_ALG_DIS_006
        zip_file = algorithm_zip_factory(
            "first.zip",
            {
                "dup/algo/__init__.py": b"",
                "dup/algo/solver.py": b"def algorithm(instance_data, timelimit):\n"
                b"    return {'ok': True}\n",
            },
        )
        auth_client.post(
            reverse("simulation:simulation_algorithm_upload"),
            {"algorithm_zip": zip_file},
        )

        dup_file = algorithm_zip_factory(
            "duplicate.zip",
            {
                "dup/algo/__init__.py": b"",
                "dup/algo/solver.py": b"def algorithm(instance_data, timelimit):\n"
                b"    return {'ok': True}\n",
            },
        )
        response = auth_client.post(
            reverse("simulation:simulation_algorithm_upload"),
            {"algorithm_zip": dup_file},
            follow=True,
        )
        msgs = [str(m) for m in response.context["messages"]]
        assert any("already exists" in m.lower() for m in msgs)

    def test_sim_alg_dis_007(self, auth_client, simulation_env, algorithm_zip_factory):
        # Scenario: SIM_ALG_DIS_007
        broken = algorithm_zip_factory(
            "broken.zip",
            {
                "user/bad/solver.py": b"def algorithm(instance_data, timelimit):\n"
                b"return {'ok': True}\n",
            },
        )
        response = auth_client.post(
            reverse("simulation:simulation_algorithm_upload"),
            {"algorithm_zip": broken},
            follow=True,
        )
        msgs = [str(m) for m in response.context["messages"]]
        assert any("invalid algorithm package" in m.lower() for m in msgs)

    def test_sim_alg_dis_008(self, auth_client, simulation_env, algorithm_zip_factory):
        # Scenario: SIM_ALG_DIS_008
        invalid_solver = algorithm_zip_factory(
            "invalid_solver.zip",
            {
                "tester/noentry/__init__.py": b"",
                "tester/noentry/solver.py": b"x = 1\n",
            },
        )
        response = auth_client.post(
            reverse("simulation:simulation_algorithm_upload"),
            {"algorithm_zip": invalid_solver},
            follow=True,
        )
        msgs = [str(m) for m in response.context["messages"]]
        assert any("invalid algorithm package" in m.lower() for m in msgs)

    def test_sim_alg_dis_009(self, client):
        # Scenario: SIM_ALG_DIS_009
        response = client.get(reverse("simulation:simulation_algorithm_upload"))
        assert response.status_code == 302
        assert "/accounts/login/" in response.url
