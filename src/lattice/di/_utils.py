import collections.abc
import inspect
import sys
import typing

from lattice.di._types import FactoryType

__all__ = ['guess_return_type']

_T = typing.TypeVar('_T')

_GENERATORS: typing.Final = {
    collections.abc.Generator,
    collections.abc.Iterator,
}
_ASYNC_GENERATORS: typing.Final = {
    collections.abc.AsyncGenerator,
    collections.abc.AsyncIterator,
}


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
    if return_type == typing.Self and (self_cls := getattr(factory, '__self__', None)):
        return typing.cast(type[_T], self_cls)

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
