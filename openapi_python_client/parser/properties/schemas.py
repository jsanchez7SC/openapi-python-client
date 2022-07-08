__all__ = [
    "Class",
    "Schemas",
    "Parameters",
    "parse_reference_path",
    "update_schemas_with_data",
    "parameter_from_reference",
]

from typing import TYPE_CHECKING, Dict, List, NewType, Tuple, Union, cast
from urllib.parse import urlparse

import attr

from ... import Config
from ... import schema as oai
from ...schema.openapi_schema_pydantic import Parameter
from ...utils import ClassName, PythonIdentifier
from ..errors import ParameterError, ParseError, PropertyError

if TYPE_CHECKING:  # pragma: no cover
    from .property import Property
else:
    Property = "Property"  # pylint: disable=invalid-name


_ReferencePath = NewType("_ReferencePath", str)


def parse_reference_path(ref_path_raw: str) -> Union[_ReferencePath, ParseError]:
    """
    Takes a raw string provided in a `$ref` and turns it into a validated `_ReferencePath` or a `ParseError` if
    validation fails.

    See Also:
        - https://swagger.io/docs/specification/using-ref/
    """
    parsed = urlparse(ref_path_raw)
    if parsed.scheme or parsed.path:
        return ParseError(detail=f"Remote references such as {ref_path_raw} are not supported yet.")
    return cast(_ReferencePath, parsed.fragment)


@attr.s(auto_attribs=True, frozen=True)
class Class:
    """Represents Python class which will be generated from an OpenAPI schema"""

    name: ClassName
    module_name: PythonIdentifier

    @staticmethod
    def from_string(*, string: str, config: Config) -> "Class":
        """Get a Class from an arbitrary string"""
        class_name = string.split("/")[-1]  # Get rid of ref path stuff
        class_name = ClassName(class_name, config.field_prefix)
        override = config.class_overrides.get(class_name)

        if override is not None and override.class_name is not None:
            class_name = ClassName(override.class_name, config.field_prefix)

        if override is not None and override.module_name is not None:
            module_name = override.module_name
        else:
            module_name = class_name
        module_name = PythonIdentifier(module_name, config.field_prefix)

        return Class(name=class_name, module_name=module_name)


@attr.s(auto_attribs=True, frozen=True)
class Schemas:
    """Structure for containing all defined, shareable, and reusable schemas (attr classes and Enums)"""

    classes_by_reference: Dict[_ReferencePath, Property] = attr.ib(factory=dict)
    classes_by_name: Dict[ClassName, Property] = attr.ib(factory=dict)
    errors: List[ParseError] = attr.ib(factory=list)


def update_schemas_with_data(
    *, ref_path: _ReferencePath, data: oai.Schema, schemas: Schemas, config: Config
) -> Union[Schemas, PropertyError]:
    """
    Update a `Schemas` using some new reference.

    Args:
        ref_path: The output of `parse_reference_path` (validated $ref).
        data: The schema of the thing to add to Schemas.
        schemas: `Schemas` up until now.
        config: User-provided config for overriding default behavior.

    Returns:
        Either the updated `schemas` input or a `PropertyError` if something went wrong.

    See Also:
        - https://swagger.io/docs/specification/using-ref/
    """
    from . import property_from_data

    prop: Union[PropertyError, Property]
    prop, schemas = property_from_data(
        data=data, name=ref_path, schemas=schemas, required=True, parent_name="", config=config
    )

    if isinstance(prop, PropertyError):
        prop.detail = f"{prop.header}: {prop.detail}"
        prop.header = f"Unable to parse schema {ref_path}"
        if isinstance(prop.data, oai.Reference) and prop.data.ref.endswith(ref_path):  # pragma: nocover
            prop.detail += (
                "\n\nRecursive and circular references are not supported. "
                "See https://github.com/openapi-generators/openapi-python-client/issues/466"
            )
        return prop

    schemas = attr.evolve(schemas, classes_by_reference={ref_path: prop, **schemas.classes_by_reference})
    return schemas


@attr.s(auto_attribs=True, frozen=True)
class Parameters:
    """Structure for containing all defined, shareable, and reusable parameters"""

    classes_by_reference: Dict[_ReferencePath, Parameter] = attr.ib(factory=dict)
    classes_by_name: Dict[ClassName, Parameter] = attr.ib(factory=dict)
    errors: List[ParseError] = attr.ib(factory=list)


def parameter_from_data(
    *,
    name: str,
    required: bool,
    data: Union[oai.Reference, oai.Parameter],
    schemas: Schemas,
    parameters: Parameters,
    config: Config,
) -> Tuple[Union[Parameter, ParameterError], Schemas, Parameters]:
    """Generates parameters from"""
    from . import property_from_data

    if isinstance(data, oai.Reference):
        return ParameterError("Unable to resolve another reference"), schemas, parameters

    if data.param_schema is None:
        return ParameterError("Parameter has no schema"), schemas, parameters

    _, new_schemas = property_from_data(
        name=name,
        required=required,
        data=data.param_schema,
        schemas=schemas,
        parent_name="",
        config=config,
    )

    new_param = Parameter(
        name=name,
        required=required,
        explode=data.explode,
        style=data.style,
        param_schema=data.param_schema,
        param_in=data.param_in,
    )
    parameters = attr.evolve(parameters, classes_by_name={**parameters.classes_by_name, name: new_param})
    return new_param, new_schemas, parameters


def update_parameters_with_data(
    *, ref_path: _ReferencePath, data: oai.Parameter, parameters: Parameters, schemas: Schemas, config: Config
) -> Union[Parameters, ParameterError]:
    """
    Update a `Schemas` using some new reference.

    Args:
        ref_path: The output of `parse_reference_path` (validated $ref).
        data: The schema of the thing to add to Schemas.
        schemas: `Schemas` up until now.
        config: User-provided config for overriding default behavior.

    Returns:
        Either the updated `schemas` input or a `PropertyError` if something went wrong.

    See Also:
        - https://swagger.io/docs/specification/using-ref/
    """
    param, schemas, parameters = parameter_from_data(
        data=data, name=data.name, parameters=parameters, schemas=schemas, required=True, config=config
    )

    if isinstance(param, ParameterError):
        param.detail = f"{param.header}: {param.detail}"
        param.header = f"Unable to parse schema {ref_path}"
        if isinstance(param.data, oai.Reference) and param.data.ref.endswith(ref_path):  # pragma: nocover
            param.detail += (
                "\n\nRecursive and circular references are not supported. "
                "See https://github.com/openapi-generators/openapi-python-client/issues/466"
            )
        return param

    parameters = attr.evolve(parameters, classes_by_reference={ref_path: param, **parameters.classes_by_reference})
    return parameters


def parameter_from_reference(
    *,
    param: Union[oai.Reference, Parameter],
    parameters: Parameters,
) -> Union[Parameter, ParameterError]:
    """
    Returns a Parameter from a Reference or the Parameter itself if one was provided.

    Args:
        param: A parameter by `Reference`.
        parameters: `Parameters` up until now.

    Returns:
        Either the updated `schemas` input or a `PropertyError` if something went wrong.

    See Also:
        - https://swagger.io/docs/specification/using-ref/
    """
    if isinstance(param, Parameter):
        return param

    ref_path = parse_reference_path(param.ref)

    if isinstance(ref_path, ParseError):
        return ParameterError(detail=ref_path.detail)

    _resolved_parameter_class = parameters.classes_by_reference.get(ref_path, None)
    if _resolved_parameter_class is None:
        return ParameterError(detail=f"Reference `{ref_path}` not found.")
    return _resolved_parameter_class
