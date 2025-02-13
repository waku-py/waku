from __future__ import annotations

import collections.abc
import functools
import inspect
import sys
import typing
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, NewType

from waku.di._markers import Inject

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from waku.di._types import FactoryType

__all__ = [
    'Dependency',
    'clear_wrapper',
    'collect_dependencies',
    'guess_return_type',
    'is_iterable_generic_collection',
]

_T = typing.TypeVar('_T')
_F = typing.Callable[..., typing.Any]

_GENERATORS: typing.Final = {
    collections.abc.Generator,
    collections.abc.Iterator,
}
_ASYNC_GENERATORS: typing.Final = {
    collections.abc.AsyncGenerator,
    collections.abc.AsyncIterator,
}

_sentinel = object()


def guess_return_type(factory: FactoryType[_T]) -> type[_T]:
    unwrapped = inspect.unwrap(factory)

    origin = typing.get_origin(factory)
    is_generic = origin and inspect.isclass(origin)
    if inspect.isclass(factory) or is_generic:
        return typing.cast(type[_T], factory)

    try:
        return_type = _get_type_hints(unwrapped)['return']
    except KeyError as e:
        msg = f'Factory {factory.__qualname__} does not specify return type.'
        raise ValueError(msg) from e
    except TypeError:
        if isinstance(factory, NewType):
            msg = f'Implementation should be added to provider for type <{factory}> created via NewType.'
            raise ValueError(msg) from None  # noqa: TRY004
        raise
    except NameError:
        # handle future annotations.
        # functions might have dependencies in them
        # and we don't have the container context here so
        # we can't call _get_type_hints
        ret_annotation = unwrapped.__annotations__['return']

        try:
            return_type = _get_return_annotation(ret_annotation, _get_fn_ns(unwrapped))
        except NameError as e:
            msg = f"Factory {factory.__qualname__} does not specify return type. Or it's type is not defined yet."
            raise ValueError(msg) from e

    if origin := typing.get_origin(return_type):
        args = typing.get_args(return_type)

        is_async_gen = origin in _ASYNC_GENERATORS and inspect.isasyncgenfunction(unwrapped)
        is_sync_gen = origin in _GENERATORS and inspect.isgeneratorfunction(
            unwrapped,
        )
        if is_async_gen or is_sync_gen:
            return_type = args[0]

    # classmethod returning `typing.Self`
    # fmt: off
    if (
        return_type == typing.Self  # pyright: ignore[reportUnknownMemberType]
        and (self_cls := getattr(factory, '__self__', None))
    ):
        return typing.cast(type[_T], self_cls)
    # fmt: on

    return typing.cast(type[_T], return_type)


def _get_type_hints(
    obj: typing.Any,
    context: dict[str, type[typing.Any]] | None = None,
) -> dict[str, typing.Any]:
    if not context:
        context = {}
    return typing.get_type_hints(obj, include_extras=True, localns=context)


def _get_fn_ns(fn: collections.abc.Callable[..., typing.Any]) -> dict[str, typing.Any]:
    return getattr(sys.modules.get(fn.__module__, None), '__dict__', {})


def _get_return_annotation(
    ret_annotation: str,
    context: dict[str, typing.Any],
) -> type[typing.Any]:
    return eval(ret_annotation, context)  # type:ignore[no-any-return] # noqa: S307


@dataclass(kw_only=True)
class Dependency(typing.Generic[_T]):
    name: str
    type_: type[_T]

    @functools.cached_property
    def inner_type(self) -> type[_T]:
        return typing.cast(
            type[_T],
            typing.get_args(self.type_)[0] if self.is_iterable else self.type_,
        )

    @functools.cached_property
    def is_iterable(self) -> bool:
        return is_iterable_generic_collection(self.type_)  # type: ignore[arg-type]


def collect_dependencies(
    dependent: typing.Callable[..., object] | dict[str, typing.Any],
    ctx: dict[str, type[typing.Any]] | None = None,
) -> typing.Iterable[Dependency[object]]:
    if not isinstance(dependent, dict):
        with _remove_annotation(dependent.__annotations__, 'return'):
            type_hints = _get_type_hints(dependent, context=ctx)
    else:
        type_hints = dependent

    for name, hint in type_hints.items():
        if typing.get_origin(hint) is Annotated:
            dep_type, args = _get_annotation_args(hint)
            inject_marker = _find_inject_marker_in_annotation_args(args)
            if inject_marker is None:
                continue
        else:
            dep_type = hint

        yield Dependency(name=name, type_=dep_type)


def _get_annotation_args(type_hint: typing.Any) -> tuple[type, tuple[typing.Any, ...]]:
    try:
        dep_type, *args = typing.get_args(type_hint)
    except ValueError:
        dep_type, args = type_hint, []
    return dep_type, tuple(args)


def _find_inject_marker_in_annotation_args(args: tuple[typing.Any, ...]) -> Inject | None:
    for arg in args:
        try:
            if issubclass(arg, Inject):
                return Inject()
        except TypeError:
            pass

        if isinstance(arg, Inject):
            return arg
    return None


def clear_wrapper(wrapper: _F) -> _F:
    inject_annotations = _get_inject_annotations(wrapper)
    signature = inspect.signature(wrapper)
    new_params = tuple(p for p in signature.parameters.values() if p.name not in inject_annotations)
    wrapper.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=new_params,
    )
    for name in inject_annotations:
        del wrapper.__annotations__[name]
    return wrapper


def _get_inject_annotations(function: Callable[..., typing.Any]) -> dict[str, typing.Any]:
    with _remove_annotation(function.__annotations__, 'return'):
        return {
            name: annotation
            for name, annotation in typing.get_type_hints(
                function,
                include_extras=True,
            ).items()
            if any(isinstance(arg, Inject) or arg is Inject for arg in typing.get_args(annotation))
        }


@contextmanager
def _remove_annotation(annotations: dict[str, typing.Any], name: str) -> Iterator[None]:
    annotation = annotations.pop(name, _sentinel)
    yield
    if annotation is not _sentinel:
        annotations[name] = annotation


@functools.cache
def is_iterable_generic_collection(type_: typing.Any) -> bool:
    if not (origin := typing.get_origin(type_)):
        return False
    return collections.abc.Iterable in inspect.getmro(origin) or issubclass(origin, collections.abc.Iterable)
