from http import HTTPStatus
from typing import override

import pytest
from django.http import JsonResponse
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
from django.urls import path
from pydantic import BaseModel, ValidationError

from viu import Json, Path, Query, Router


class QueryParams(BaseModel):
    id: int
    name: str


class PathParams(BaseModel):
    age: int
    name: str


class Payload(BaseModel):
    name: str
    age: int


router = Router()


@router.get("/query")
def get_from_query_params(params: Query[QueryParams]) -> JsonResponse:
    return JsonResponse(status=200, data={"id": params.id, "name": params.name})


@router.get("/path/<name>/with/<age>")
def get_from_path_params(params: Path[PathParams]) -> JsonResponse:
    return JsonResponse(status=200, data={"age": params.age, "name": params.name})


@router.post("/post")
def post_stuff() -> JsonResponse:
    return JsonResponse({}, status=200)


@router.post("/post-json")
def post_json(payload: Json[Payload]) -> JsonResponse:
    return JsonResponse({"name": payload.name, "age": payload.age}, status=200)


@router.route(path="/route")
def get_from_route() -> JsonResponse:
    return JsonResponse({}, status=200)


@router.route(path="/route-with-method-restrictions", methods={"GET"})
def get_from_route_for_all_methods() -> JsonResponse:
    return JsonResponse({}, status=200)


urlpatterns = [path("", router.urls)]


@override_settings(ROOT_URLCONF="tests.test_viu")
class TestRouter(SimpleTestCase):
    def test_get(self):
        response = Client().get("/query?id=2&name=joe")
        assert response.status_code == HTTPStatus.OK
        assert response.json() == {"id": 2, "name": "joe"}

    def test_post(self):
        response = Client().post("/post")
        assert response.status_code == HTTPStatus.OK
        assert response.json() == {}

    def test_post_json(self):
        response = Client().post(
            "/post-json",
            data={"name": "Averell", "age": 30},
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json() == {"name": "Averell", "age": 30}

    def test_route(self):
        response = Client().get("/route")
        assert response.status_code == HTTPStatus.OK
        assert response.json() == {}

        response = Client().post("/route")
        assert response.status_code == HTTPStatus.OK
        assert response.json() == {}

        response = Client().patch("/route")
        assert response.status_code == HTTPStatus.OK
        assert response.json() == {}

    def test_route_with_method_restrictions(self):
        response = Client().get("/route-with-method-restrictions")
        assert response.status_code == HTTPStatus.OK
        assert response.json() == {}

        response = Client().post("/route-with-method-restrictions")
        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
        assert response.json() == {}

        response = Client().patch("/route-with-method-restrictions")
        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
        assert response.json() == {}

    def test_with_path_params(self):
        response = Client().get("/path/averell/with/33")
        assert response.status_code == HTTPStatus.OK

    def test_not_found(self):
        response = Client().get("/not-found")
        assert response.status_code == HTTPStatus.NOT_FOUND
        # TODO: 404 are return by django itself, therefore the
        # content type is html.
        # Should we replace the django routing system entirely (very meh) or
        # just let the user handle it with handler404 ?
        # Cf:
        # - https://docs.djangoproject.com/en/5.2/topics/http/views/#the-http404-exception
        # - https://docs.djangoproject.com/en/5.2/topics/http/views/#customizing-error-views


class TestQuery(SimpleTestCase):
    @override
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.request_factory = RequestFactory()

    def test_works(self):
        request = self.request_factory.get("/?id=1&name=toto")

        response = get_from_query_params(request)

        assert response.status_code == 200
        assert response.content == b'{"id": 1, "name": "toto"}'

    def test_fails(self):
        request = self.request_factory.get("/?id=toto&name=1")

        with pytest.raises(ValidationError):
            get_from_query_params(request)


@override_settings(ROOT_URLCONF="tests.test_viu")
class TestJson(SimpleTestCase):
    @override
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.request_factory = RequestFactory()

    def test_works(self):
        request = self.request_factory.post(
            "/post-json",
            data={"name": "Averell", "age": 30},
            content_type="application/json",
        )

        response = post_json(request)

        assert response.status_code == 200
        assert response.content == b'{"name": "Averell", "age": 30}'

    def test_fails(self):
        request = self.request_factory.post(
            "/post-json",
            data={},
            content_type="application/json",
        )

        with pytest.raises(ValidationError):
            post_json(request)
