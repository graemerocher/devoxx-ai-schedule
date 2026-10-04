"""Shared pytest fixtures: one Micronaut application context per test module."""

import pytest
import requests

from pyronaut.test import MicronautTest, micronaut_test_fixture


def start_context(request, **properties):
    return micronaut_test_fixture(request, MicronautTest(environments=["test"], properties=properties))


@pytest.fixture(scope="module")
def app_context(request):
    properties = getattr(request.module, "CONTEXT_PROPERTIES", {})
    fixture = start_context(request, **properties)
    yield fixture
    fixture.stop()


@pytest.fixture(scope="module")
def client(app_context):
    return requests.with_context(app_context)
