from http import HTTPStatus
from typing import (
    Annotated,
    Callable,
    Concatenate,
    Literal,
    Protocol,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from django.http import HttpResponse
from django.http.request import HttpRequest
from django.http.response import JsonResponse
from django.urls import URLPattern, URLResolver
from django.urls import path as django_path
from pydantic import BaseModel
from typing_extensions import Any

type Path[T: BaseModel] = Annotated[T, "from-path-params"]
type Query[T: BaseModel] = Annotated[T, "from-query-params"]
type Json[T: BaseModel] = Annotated[T, "from-json-payload"]


class Extractor[T](Protocol):
    def from_request(self, request: HttpRequest) -> T: ...


class PathExtractor[T: BaseModel]:
    def __init__(self, output_type: type[T]) -> None:
        self.output_type = output_type

    def from_request(self, request: HttpRequest) -> T:
        resolver_match = request.resolver_match
        assert resolver_match is not None
        # NB: `captured_kwargs` is not defined as a ResolverMatch field in django-types
        captured_kwargs = cast(dict[str, Any], resolver_match.captured_kwargs)  # pyrefly: ignore[missing-attribute]
        return self.output_type.model_validate(captured_kwargs)


class QueryExtractor[T: BaseModel]:
    def __init__(self, output_type: type[T]) -> None:
        self.output_type = output_type

    def from_request(self, request: HttpRequest) -> T:
        return self.output_type(**request.GET.dict())


class JsonExtractor[T: BaseModel]:
    def __init__(self, output_type: type[T]) -> None:
        self.output_type = output_type

    def from_request(self, request: HttpRequest) -> T:
        return self.output_type.model_validate_json(request.body)


type DjangoView = Callable[Concatenate[HttpRequest, ...], HttpResponse]
type AnyHandler = Callable[..., HttpResponse]
type HttpMethod = Literal["GET"] | Literal["POST"]
type Extractors = dict[str, Extractor[BaseModel]]


class Router:
    def __init__(self) -> None:
        self._views = list[URLPattern | URLResolver]()

    def get(self, path: str):
        return self.route(methods={"GET"}, path=path)

    def post(self, path: str):
        return self.route(methods={"POST"}, path=path)

    def route(self, path: str, methods: set[HttpMethod] | None = None):

        def wrapper[**P](handler: AnyHandler) -> DjangoView:
            print(handler.__name__)
            extractors = self._get_extractors(handler)

            def django_view(request: HttpRequest, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
                if methods and request.method not in methods:
                    return JsonResponse({}, status=HTTPStatus.METHOD_NOT_ALLOWED)

                inputs = {
                    argument_name: extractors[argument_name].from_request(request)
                    for argument_name, extractor in extractors.items()
                }
                return handler(*args, **inputs)

            _path = path.removeprefix("/")
            self._views.append(django_path(_path, django_view))
            return django_view

        return wrapper

    def _get_extractors(self, handler: AnyHandler) -> Extractors:
        annotations = get_type_hints(handler, include_extras=True)
        _return_type = annotations.pop("return")
        parameters_types = annotations

        extractors: Extractors = {}
        for parameter_name, parameter_type in parameters_types.items():
            alias = get_origin(parameter_type)
            assert alias is not None
            type_args = get_args(parameter_type)[0]
            assert issubclass(type_args, BaseModel)
            metadata = alias.__value__.__metadata__[0]
            if metadata == "from-path-params":
                extractors[parameter_name] = PathExtractor(type_args)
            elif metadata == "from-query-params":
                extractors[parameter_name] = QueryExtractor(type_args)
            elif metadata == "from-json-payload":
                extractors[parameter_name] = JsonExtractor(type_args)
            print(f"\tArgument name => {parameter_name}")
            print(f"\tType name     => {alias.__name__}")
            print(f"\tType args     => {type_args}")
            print(f"\tType metadata =>{metadata}")

        return extractors

    @property
    def urls(self) -> tuple[list[URLResolver | URLPattern], str, str]:
        return (self._views, "", "")
