import inspect
from abc import ABC, abstractmethod
from http import HTTPStatus
from typing import (
    Annotated,
    Any,
    Callable,
    Concatenate,
    Literal,
    TypeAliasType,
    cast,
    get_args,
    get_origin,
    override,
)

from django.http import HttpResponse
from django.http.request import HttpRequest
from django.http.response import JsonResponse
from django.urls import URLPattern, URLResolver
from django.urls import path as django_path
from django.views import View
from pydantic import BaseModel


class Extractor[T](ABC):
    @abstractmethod
    def from_request(self, request: HttpRequest) -> T: ...


class PathExtractor[T: BaseModel](Extractor[T]):
    def __init__(self, output_type: type[T]) -> None:
        assert issubclass(output_type, BaseModel)
        self.output_type = output_type

    @override
    def from_request(self, request: HttpRequest) -> T:
        resolver_match = request.resolver_match
        assert resolver_match is not None
        # NB: `captured_kwargs` is not defined as a ResolverMatch field in django-types
        captured_kwargs = cast(dict[str, Any], resolver_match.captured_kwargs)  # pyrefly: ignore[missing-attribute]
        return self.output_type.model_validate(captured_kwargs)


class QueryParamsExtractor[T: BaseModel](Extractor[T]):
    def __init__(self, output_type: type[T]) -> None:
        assert issubclass(output_type, BaseModel)
        self.output_type = output_type

    @override
    def from_request(self, request: HttpRequest) -> T:
        return self.output_type(**request.GET.dict())


class JsonPayloadExtractor[T: BaseModel](Extractor[T]):
    def __init__(self, output_type: type[T]) -> None:
        assert issubclass(output_type, BaseModel)
        self.output_type = output_type

    @override
    def from_request(self, request: HttpRequest) -> T:
        return self.output_type.model_validate_json(request.body)


class RequestExtractor[T: HttpRequest](Extractor[T]):
    def __init__(self, output_type: type[T]) -> None:
        assert issubclass(output_type, HttpRequest)
        self.output_type = output_type

    @override
    def from_request(self, request: HttpRequest) -> T:
        assert isinstance(request, self.output_type)
        return request


type Path[T: BaseModel] = Annotated[T, PathExtractor[T]]
type Query[T: BaseModel] = Annotated[T, QueryParamsExtractor[T]]
type Json[T: BaseModel] = Annotated[T, JsonPayloadExtractor[T]]
type Raw[T] = Annotated[HttpRequest, RequestExtractor[T]]

type DjangoView = Callable[Concatenate[HttpRequest, ...], HttpResponse]
type AnyHandler = Callable[..., HttpResponse]
type HttpMethod = Literal["GET"] | Literal["POST"]
type Extractors = dict[str, Extractor[Any]]
type DjangoViewClass = type[View]


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

    def route_view(self, path: str):
        def wrapper(viu: DjangoViewClass) -> DjangoViewClass:
            print(viu.__name__)
            extractors_per_method = dict[str, Extractors]()
            for method_name in viu.http_method_names:
                handler = cast(AnyHandler | None, getattr(viu, method_name, None))

                # Mimic Django View's behaviour:
                # Use the "get" handler when no "head" handler is defined
                if method_name == "head" and handler is None:
                    handler = cast(AnyHandler, getattr(viu, "get", None))

                if handler is None:
                    continue

                # OPTIONS request handler is defined by Django's View. I don't know yet how to handle
                # the case where a user override it with viu-style parameters injection.
                if method_name == "options":
                    continue

                extractors = self._get_extractors(handler)
                extractors_per_method[method_name.upper()] = extractors

            setattr(viu, "_extractors_per_method", extractors_per_method)

            def viu_dispatch(self: View, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
                # Mimic Django View's dispatch behaviour:
                # Try to dispatch to the right method; if a method doesn't exist,
                # defer to the error handler. Also defer to the error handler if the
                # request method isn't on the approved list.

                method = request.method.lower()  # pyrefly: ignore[missing-attribute] HttpRequest.method is not nullable in dispatch

                if method not in self.http_method_names or (handler := getattr(self, method, None)) is None:
                    return self.http_method_not_allowed(request, *args, **kwargs)

                # OPTIONS request handler is defined by Django's View. I don't know yet how to handle
                # the case where a user override it with viu-style parameters injection.
                if method == "options":
                    return handler(request, *args, **kwargs)

                # HttpRequest.method is not None when the dispatch method is called
                extractors = cast(Extractors, self._extractors_per_method[request.method])  # pyrefly: ignore[missing-attribute]
                inputs = {
                    argument_name: extractors[argument_name].from_request(request)
                    for argument_name, extractor in extractors.items()
                }
                return handler(**inputs)

            viu.dispatch = viu_dispatch
            _path = path.removeprefix("/")
            self._views.append(django_path(_path, viu.as_view()))
            return viu

        return wrapper

    @property
    def urls(self) -> tuple[list[URLResolver | URLPattern], str, str]:
        return (self._views, "", "")

    def _get_extractors(self, handler: AnyHandler) -> Extractors:
        extractors: Extractors = {}
        signature = inspect.signature(handler)
        for parameter_name, parameter in signature.parameters.items():
            if parameter_name == "self":
                continue

            parameter_annotation = parameter.annotation
            if parameter_name != "self" and parameter_annotation is inspect._empty:
                raise Exception("Every parameter must have a type annotations")

            origin = get_origin(parameter_annotation)
            if isinstance(origin, TypeAliasType):
                annotated_alias = origin.__value__
            else:
                breakpoint()
                raise Exception("Unexpected type annotation A")

            assert get_origin(annotated_alias) is Annotated
            metadata = annotated_alias.__metadata__[0]

            if (origin := get_origin(metadata)) is not None and issubclass(origin, Extractor):
                extractor_class = origin
            elif issubclass(metadata, Extractor):
                extractor_class = metadata
            else:
                raise Exception("unexpected type annotation B")

            extractor_class = cast(type[Extractor[Any]], extractor_class)

            extractor_output_type = get_args(parameter_annotation)[0]

            extractors[parameter_name] = extractor_class(extractor_output_type)  # pyrefly: ignore[bad-argument-count]

        return extractors
