"""Calculation of the least upper bound types (joins)."""

from typing import cast, List

from mypy.types import (
    Type, AnyType, NoneTyp, Void, TypeVisitor, Instance, UnboundType,
    ErrorType, TypeVar, Callable, TupleType, ErasedType, BasicTypes, TypeList
)
from mypy.subtypes import is_subtype, is_equivalent, map_instance_to_supertype


def join_types(s: Type, t: Type, basic: BasicTypes) -> Type:
    """Return the least upper bound of s and t.

    For example, the join of 'int' and 'object' is 'object'.

    If the join does not exist, return an ErrorType instance.
    """
    
    if isinstance(s, AnyType):
        return s
    
    if isinstance(s, NoneTyp) and not isinstance(t, Void):
        return t

    if isinstance(s, ErasedType):
        return t

    # Use a visitor to handle non-trivial cases.
    return t.accept(TypeJoinVisitor(s, basic))


class TypeJoinVisitor(TypeVisitor[Type]):
    """Implementation of the least upper bound algorithm."""
    
    def __init__(self, s: Type, basic: BasicTypes) -> None:
        self.s = s
        self.basic = basic
        self.object = basic.object
    
    def visit_unbound_type(self, t: UnboundType) -> Type:
        if isinstance(self.s, Void) or isinstance(self.s, ErrorType):
            return ErrorType()
        else:
            return AnyType()
    
    def visit_error_type(self, t: ErrorType) -> Type:
        return t
    
    def visit_type_list(self, t: TypeList) -> Type:
        assert False, 'Not supported'
    
    def visit_any(self, t: AnyType) -> Type:
        return t
    
    def visit_void(self, t: Void) -> Type:
        if isinstance(self.s, Void):
            return t
        else:
            return ErrorType()
    
    def visit_none_type(self, t: NoneTyp) -> Type:
        if not isinstance(self.s, Void):
            return self.s
        else:
            return self.default(self.s)
    
    def visit_erased_type(self, t: ErasedType) -> Type:
        return self.s
    
    def visit_type_var(self, t: TypeVar) -> Type:
        if isinstance(self.s, TypeVar) and (cast(TypeVar, self.s)).id == t.id:
            return self.s
        else:
            return self.default(self.s)
    
    def visit_instance(self, t: Instance) -> Type:
        if isinstance(self.s, Instance):
            return join_instances(t, cast(Instance, self.s), self.basic)
        elif t.type == self.basic.type_type.type and is_subtype(self.s, t):
            return t
        else:
            return self.default(self.s)
    
    def visit_callable(self, t: Callable) -> Type:
        if isinstance(self.s, Callable) and is_similar_callables(
                                                    t, cast(Callable, self.s)):
            return combine_similar_callables(t, cast(Callable, self.s),
                                             self.basic)
        elif t.is_type_obj() and is_subtype(self.s, self.basic.type_type):
            return self.basic.type_type
        elif (isinstance(self.s, Instance) and
                  cast(Instance, self.s).type == self.basic.type_type.type and
                  t.is_type_obj()):
            return self.basic.type_type
        else:
            return self.default(self.s)
    
    def visit_tuple_type(self, t: TupleType) -> Type:
        if (isinstance(self.s, TupleType) and
                cast(TupleType, self.s).length() == t.length()):
            items = [] # type: List[Type]
            for i in range(t.length()):
                items.append(self.join(t.items[i],
                                       (cast(TupleType, self.s)).items[i]))
            return TupleType(items)
        else:
            return self.default(self.s)
    
    def join(self, s: Type, t: Type) -> Type:
        return join_types(s, t, self.basic)
    
    def default(self, typ: Type) -> Type:
        if isinstance(typ, UnboundType):
            return AnyType()
        elif isinstance(typ, Void) or isinstance(typ, ErrorType):
            return ErrorType()
        else:
            return self.object


def join_instances(t: Instance, s: Instance, basic: BasicTypes) -> Type:
    """Calculate the join of two instance types.

    If allow_interfaces is True, also consider interface-type results for
    non-interface types.
    
    Return ErrorType if the result is ambiguous.
    """
    
    if t.type == s.type:
        # Simplest case: join two types with the same base type (but
        # potentially different arguments).
        if is_subtype(t, s):
            # Compatible; combine type arguments.
            args = [] # type: List[Type]
            for i in range(len(t.args)):
                args.append(join_types(t.args[i], s.args[i], basic))
            return Instance(t.type, args)
        else:
            # Incompatible; return trivial result object.
            return basic.object
    elif t.type.bases and is_subtype(t, s):
        return join_instances_via_supertype(t, s, basic)
    else:
        # Now t is not a subtype of s, and t != s. Now s could be a subtype
        # of t; alternatively, we need to find a common supertype. This works
        # in of the both cases.
        return join_instances_via_supertype(s, t, basic)


def join_instances_via_supertype(t: Instance, s: Instance,
                                 basic: BasicTypes) -> Type:
    # Give preference to joins via duck typing relationship, so that
    # join(int, float) == float, for example.
    if t.type.ducktype and is_subtype(t.type.ducktype, s):
        return join_types(t.type.ducktype, s, basic)
    elif s.type.ducktype and is_subtype(s.type.ducktype, t):
        return join_types(t, s.type.ducktype, basic)
    res = s
    mapped = map_instance_to_supertype(t, t.type.bases[0].type)
    join = join_instances(mapped, res, basic)
    # If the join failed, fail. This is a defensive measure (this might
    # never happen).
    if isinstance(join, ErrorType):
        return join
    # Now the result must be an Instance, so the cast below cannot fail.
    res = cast(Instance, join)
    return res


def is_similar_callables(t: Callable, s: Callable) -> bool:
    """Return True if t and s are equivalent and have identical numbers of
    arguments, default arguments and varargs.
    """
    
    return (len(t.arg_types) == len(s.arg_types) and t.min_args == s.min_args
            and t.is_var_arg == s.is_var_arg and is_equivalent(t, s))


def combine_similar_callables(t: Callable, s: Callable,
                              basic: BasicTypes) -> Callable:
    arg_types = [] # type: List[Type]
    for i in range(len(t.arg_types)):
        arg_types.append(join_types(t.arg_types[i], s.arg_types[i], basic))
    # TODO kinds and argument names
    return Callable(arg_types,
                    t.arg_kinds,
                    t.arg_names,
                    join_types(t.ret_type, s.ret_type, basic),
                    t.is_type_obj() and s.is_type_obj(),
                    None,
                    t.variables)
    return s
