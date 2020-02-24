# Copyright (C) 2020 Linaro Limited
#
# Author: Antonio Terceiro <antonio.terceiro@linaro.org>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.

import pytest
import re
import time
from pathlib import Path
from tests.lava_dispatcher.test_basic import Factory


@pytest.fixture
def factory():
    return Factory()


@pytest.fixture
def job(factory):
    return factory.create_job(
        "hi6220-hikey-r2-01.jinja2", "sample_jobs/docker-test.yaml"
    )


@pytest.fixture
def action(job):
    return job.pipeline.actions[2]


def test_validate_schema(factory):
    factory.validate_job_strict = True
    # The next call not crashing means that the strict schema validation
    # passed.
    factory.create_job("hi6220-hikey-r2-01.jinja2", "sample_jobs/docker-test.yaml")


def test_detect_correct_action(action):
    assert type(action).__name__ == "DockerTestAction"


def test_run(action, mocker):
    mocker.patch("lava_dispatcher.utils.fastboot.DockerDriver.__get_device_nodes__")
    ShellCommand = mocker.patch("lava_dispatcher.actions.test.docker.ShellCommand")
    ShellSesssion = mocker.patch("lava_dispatcher.actions.test.docker.ShellSession")
    docker_connection = mocker.MagicMock()
    ShellSesssion.return_value = docker_connection
    action_run = mocker.patch("lava_dispatcher.actions.test.docker.TestShellAction.run")
    connection = mocker.MagicMock()
    add_device_container_mapping = mocker.patch(
        "lava_dispatcher.actions.test.docker.add_device_container_mapping"
    )

    action.validate()
    action.run(connection, time.time() + 1000)

    # device is shared with the container
    add_device_container_mapping.assert_called_with(
        job_id=action.job.job_id,
        device_info={"board_id": "0123456789"},
        container=mocker.ANY,
        container_type="docker",
        logging_info=mocker.ANY,
    )

    # overlay gets created
    overlay = next(Path(action.job.tmp_dir).glob("lava-create-overlay-*/lava-*"))
    assert overlay.exists()
    # overlay gets the correct content
    lava_test_runner = overlay / "bin" / "lava-test-runner"
    assert lava_test_runner.exists()
    lava_test_0 = overlay / "0"
    assert lava_test_runner.exists()

    environment = overlay / "environment"
    assert "ANDROID_SERIAL='0123456789'" in environment.open().read()

    # docker gets called
    docker_call = ShellCommand.mock_calls[0][1][0]
    assert docker_call.startswith("docker run")
    # overlay gets passed into docker
    assert (
        re.match(
            r".* --mount=type=bind,source=%s,destination=/%s" % (overlay, overlay.name),
            docker_call,
        )
        is not None
    )

    # the lava-test-shell implementation gets called with the docker shell
    action_run.assert_called_with(docker_connection, mocker.ANY)
