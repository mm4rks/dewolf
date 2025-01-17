from __future__ import annotations

import logging
from itertools import product
from typing import TYPE_CHECKING, Dict, Generic, Iterator, List, Sequence, Set, Tuple, TypeVar

import decompiler.structures.pseudo as pseudo
from decompiler.structures.logic.logic_interface import ConditionInterface, PseudoLogicInterface
from decompiler.structures.pseudo import Condition
from simplifier.operations import BitwiseAnd, BitwiseNegate, BitwiseOr
from simplifier.visitor import ToCnfVisitor, ToDnfVisitor
from simplifier.visitor.serialize_visitor import SerializeVisitor
from simplifier.world.nodes import BaseVariable, BitVector, Constant, Operation, TmpVariable, Variable, WorldObject
from simplifier.world.world import World

if TYPE_CHECKING:
    from decompiler.structures.ast.condition_symbol import ConditionHandler

LOGICCLASS = TypeVar("LOGICCLASS", bound="CustomLogicCondition")
PseudoLOGICCLASS = TypeVar("PseudoLOGICCLASS", bound="PseudoCustomLogicCondition")


class CustomLogicCondition(ConditionInterface, Generic[LOGICCLASS]):
    """Class in charge of implementing generic logic operations using costume logic."""

    def __init__(self, condition: WorldObject, tmp: bool = False):
        if isinstance(condition, Variable):
            self._variable = condition
        else:
            self._variable: BaseVariable = condition.world.new_variable(condition.size, tmp)
            self.context.define(self._variable, condition)

    @classmethod
    def generate_new_context(cls) -> World:
        """Generate a context for z3-conditions."""
        return World()

    @property
    def _condition(self) -> WorldObject:
        if term := self.context.get_definition(self._variable):
            return term
        return self._variable

    def __len__(self) -> int:
        """Return the length of a formula, which corresponds to its complexity."""
        if isinstance(self._condition, Variable):
            return 1
        count = 0
        for node in self.context.iter_postorder(self._condition):
            if not isinstance(node, Operation):
                continue
            count += sum(1 for op in node.operands if isinstance(op, Variable))
        return count

    def __str__(self) -> str:
        """Return a string representation."""
        condition = self._condition
        if isinstance(condition, Constant) and condition.size == 1:
            return "false" if condition.unsigned == 0 else "true"
        return str(condition)

    def copy(self) -> LOGICCLASS:
        """Copy an instance of the Z3ConditionInterface."""
        return self.__class__(self._condition)

    @classmethod
    def initialize_symbol(cls, name: str, context: World) -> LOGICCLASS:
        """Create a symbol."""
        return cls(context.variable(name, 1))

    @classmethod
    def initialize_true(cls, context: World) -> LOGICCLASS:
        """Return condition tag that represents True."""
        return cls(context.constant(1, 1))

    @classmethod
    def initialize_false(cls, context: World) -> LOGICCLASS:
        """Return condition tag that represents False."""
        return cls(context.constant(0, 1))

    @classmethod
    def disjunction_of(cls, clauses: Sequence[LOGICCLASS]) -> LOGICCLASS:
        """Create a disjunction for the list of given clauses."""
        world = clauses[0].context
        return cls(world.bitwise_or(*(clause._condition for clause in clauses)))

    @classmethod
    def conjunction_of(cls, clauses: Sequence[LOGICCLASS]) -> LOGICCLASS:
        """Create a conjunction for the list of given clauses."""
        world = clauses[0].context
        return cls(world.bitwise_and(*(clause._condition for clause in clauses)))

    def __and__(self, other: LOGICCLASS) -> LOGICCLASS:
        """Logical and of two condition tag interfaces."""
        return self.__class__(self.context.bitwise_and(self._condition, other._condition))

    def __or__(self, other: LOGICCLASS) -> LOGICCLASS:
        """Logical or of two condition tag interfaces."""
        return self.__class__(self.context.bitwise_or(self._condition, other._condition))

    def __invert__(self) -> LOGICCLASS:
        """Logical negate of two condition tag interfaces."""
        return self.__class__(self._custom_negate(self._condition))

    def _custom_negate(self, condition: WorldObject) -> WorldObject:
        """Negate the given world object."""
        if isinstance(condition, BitwiseNegate):
            return condition.operand
        return self.context.bitwise_negate(condition)

    @property
    def context(self) -> World:
        """Return context of logic condition."""
        return self._variable.world

    @property
    def is_true(self) -> bool:
        """Check whether the tag is the 'true-symbol'."""
        return isinstance(self._condition, Constant) and self._condition.unsigned != 0

    @property
    def is_false(self) -> bool:
        """Check whether the tag is the 'false-symbol'."""
        return isinstance(self._condition, Constant) and self._condition.unsigned == 0

    @property
    def is_disjunction(self) -> bool:
        """Check whether the condition is a disjunction of conditions, i.e. A v B v C."""
        return isinstance(self._condition, BitwiseOr)

    @property
    def is_conjunction(self) -> bool:
        """Check whether the condition is a conjunction of conditions, i.e. A ^ B ^ C."""
        return isinstance(self._condition, BitwiseAnd)

    @property
    def is_negation(self) -> bool:
        """Check whether the condition is a negation of conditions, i.e. !A."""
        return isinstance(self._condition, BitwiseNegate)

    @property
    def operands(self) -> List[LOGICCLASS]:
        """Return all operands of the condition."""
        return self._get_operands()

    def _get_operands(self, tmp: bool = False) -> List[LOGICCLASS]:
        """Get operands."""
        condition = self._condition
        if isinstance(condition, BitVector):
            return []
        assert isinstance(condition, Operation), f"The condition must be an operation."
        return [self.__class__(operand, tmp) for operand in condition.operands]

    @property
    def is_symbol(self) -> bool:
        """Check whether the object is a symbol."""
        return self._is_symbol(self._condition)

    @property
    def is_literal(self) -> bool:
        """Check whether the object is a literal, i.e., a symbol or a negated symbol"""
        return self._is_literal(self._condition)

    @property
    def is_disjunction_of_literals(self) -> bool:
        """
        Check whether the given condition is a disjunction of literals, i.e., whether it is
            - a symbol,
            - the negation of a symbol or
            - a disjunction of symbols or negation of symbols.
        """
        return self._is_disjunction_of_literals(self._condition)

    @property
    def is_cnf_form(self) -> bool:
        """Check whether the condition is already in cnf-form."""
        if self.is_true or self.is_false or self.is_disjunction_of_literals:
            return True
        return self.is_conjunction and all(self._is_disjunction_of_literals(clause) for clause in self._condition.operands)

    def is_equal_to(self, other: LOGICCLASS) -> bool:
        """Check whether the conditions are equal, i.e., have the same from except the ordering."""
        return World.compare(self._condition, other._condition)

    def does_imply(self, other: LOGICCLASS) -> bool:
        """Check whether the condition implies the given condition."""
        tmp_condition = self.__class__(self.context.bitwise_or(self._custom_negate(self._condition), other._condition))
        self.context.free_world_condition(tmp_condition._variable)
        tmp_condition._variable.simplify()
        does_imply_value = tmp_condition.is_true
        self.context.cleanup([tmp_condition._variable])
        return does_imply_value

    def to_cnf(self) -> LOGICCLASS:
        """Bring the condition tag into cnf-form."""
        if self.is_cnf_form:
            return self
        self.context.free_world_condition(self._variable)
        ToCnfVisitor(self._variable)
        return self

    def to_dnf(self) -> LOGICCLASS:
        """Bring the condition tag into dnf-form."""
        dnf_form = self.copy()
        self.context.free_world_condition(dnf_form._variable)
        ToDnfVisitor(dnf_form._variable)
        return dnf_form

    def simplify(self) -> LOGICCLASS:
        """Simplify the given condition. Make sure that it does not destroy cnf-form."""
        if isinstance(self._variable, Variable):
            self.context.free_world_condition(self._variable)
            self._variable.simplify()
        else:
            new_var = self.context.variable(f"Simplify", 1)
            self.context.define(new_var, self._condition)
            self.context.free_world_condition(new_var)
            new_var.simplify()
            self._variable = self.context.new_variable(1, tmp=True)
            self.context.substitute(new_var, self._variable)
        return self

    def get_symbols(self) -> Iterator[LOGICCLASS]:
        """Return all symbols used by the condition."""
        for symbol in self._get_symbols(self._condition):
            yield self.__class__(symbol)

    def get_symbols_as_string(self) -> Iterator[str]:
        """Return all symbols as strings."""
        for symbol in self._get_symbols(self._condition):
            yield str(symbol)

    def get_literals(self) -> Iterator[LOGICCLASS]:
        """Return all literals used by the condition."""
        for literal in self._get_literals(self._condition):
            yield self.__class__(literal)

    def substitute_by_true(self, condition: LOGICCLASS) -> LOGICCLASS:
        """
        Substitutes the given condition by true.

        Example: substituting in the expression (a∨b)∧c the condition (a∨b) by true results in the condition c,
             and substituting the condition c by true in the condition (a∨b)
        """
        assert self.context == condition.context, f"The condition must be contained in the same graph."
        if not self.is_true and (self.is_equal_to(condition) or condition.does_imply(self)):
            self._replace_condition_by_true()
            return self

        self.to_cnf()
        if self.is_true or self.is_false or self.is_negation or self.is_symbol:
            return self

        condition_operands: List[LOGICCLASS] = condition._get_operands()
        operands: List[LOGICCLASS] = self._get_operands()
        numb_of_arg_expr: int = len(operands) if self.is_conjunction else 1
        numb_of_arg_cond: int = len(condition_operands) if condition.is_conjunction else 1

        if numb_of_arg_expr <= numb_of_arg_cond:
            self.context.cleanup()
            return self

        subexpressions: List[LOGICCLASS] = [condition] if numb_of_arg_cond == 1 else condition_operands
        self._replace_subexpressions_by_true(subexpressions)
        to_remove = [cond._variable for cond in condition_operands + operands if cond._variable != cond._condition]
        self.context.cleanup(to_remove)
        return self

    def _replace_subexpressions_by_true(self, subexpressions: List[LOGICCLASS]):
        """Replace each clause of the Custom-Condition by True, if it is contained in the list of given subexpressions."""
        for sub_expr_1, sub_expr_2 in product(subexpressions, self.operands):
            if sub_expr_1.is_equivalent_to(sub_expr_2):
                relations = self.context.get_relation(self._condition, sub_expr_2._condition)
                for relation in relations:
                    self.context.remove_operand(self._condition, relation.sink)

    def _replace_condition_by_true(self) -> None:
        """Replace the Custom Logic condition by True."""
        if self.is_symbol:
            self._variable: BaseVariable = self.context.new_variable(self._condition.size)
            self.context.define(self._variable, self.context.constant(1, 1))
        else:
            self.context.replace(self._condition, self.context.constant(1, 1))
        self.context.cleanup()

    def remove_redundancy(self, condition_handler: ConditionHandler) -> LOGICCLASS:
        """
        Simplify conditions by considering the pseudo-conditions (more advanced simplification).

        - The given formula is simplified using the given dictionary that maps to each symbol a pseudo-condition.
        - This helps, for example for finding switch cases, because it simplifies the condition
          'x1 & x2' if 'x1 = var < 10' and 'x2 = var == 5' to the condition 'x2'.
        """
        if self.is_literal or self.is_true or self.is_false:
            return self
        assert isinstance(self._condition, Operation), "We only remove redundancy for operations"

        real_condition, compared_expressions = self._replace_symbols_by_real_conditions(condition_handler)

        self.context.free_world_condition(real_condition._variable)
        real_condition.simplify()

        self._replace_real_conditions_by_symbols(real_condition, compared_expressions, condition_handler)

        self.context.replace(self._condition, real_condition._condition)
        self.context.cleanup()
        return self

    def _replace_real_conditions_by_symbols(
        self,
        real_condition: PseudoCustomLogicCondition,
        compared_expressions: Dict[Variable, pseudo.Expression],
        condition_handler: ConditionHandler,
    ):
        """Replace all clauses of the given real-condition by symbols."""
        non_logic_operands = {
            node
            for node in self.context.iter_postorder(real_condition._variable)
            if isinstance(node, Operation) and not isinstance(node, (BitwiseOr, BitwiseAnd, BitwiseNegate))
        }
        replacement_dict = {
            real_cond._condition: symbol._condition
            for symbol, real_cond in condition_handler.get_z3_condition_map().items()
            if any(operand in compared_expressions for operand in real_cond._condition.operands)
        }
        for operand in non_logic_operands:
            negated_operand = operand.copy_tree().negate()
            for condition, symbol in replacement_dict.items():
                if World.compare(condition, operand):
                    self.context.replace(operand, symbol)
                    break
                if World.compare(condition, negated_operand):
                    self.context.replace(operand, self.context.bitwise_negate(symbol))
                    break
            else:
                new_operands = list()
                for op in operand.operands:
                    if op in compared_expressions:
                        new_operands.append(compared_expressions[op])
                    else:
                        assert isinstance(op, Constant), f"The operand must be a Constant"
                        new_operands.append(pseudo.Constant(op.signed, pseudo.Integer(op.size, signed=True)))
                condition_symbol = condition_handler.add_condition(Condition(self.OPERAND_MAPPING[operand.SYMBOL], new_operands))
                self.context.replace(operand, condition_symbol._condition)

    def _replace_symbols_by_real_conditions(
        self, condition_handler: ConditionHandler
    ) -> Tuple[PseudoCustomLogicCondition, Dict[Variable, pseudo.Expression]]:
        """
        Return the real condition where the symbols are replaced by the conditions of the condition handler
        as well as a mapping between the replaced symbols and the corresponding pseudo-expression.
        """
        copied_condition = PseudoCustomLogicCondition(self._condition)
        self.context.free_world_condition(copied_condition._variable)
        condition_nodes = set(self.context.iter_postorder(copied_condition._variable))
        compared_expressions: Dict[Variable, pseudo.Expression] = dict()
        for symbol in self.get_symbols():
            pseudo_condition: Condition = condition_handler.get_condition_of(symbol)
            for operand in pseudo_condition.operands:
                if not isinstance(operand, pseudo.Constant):
                    compared_expressions[self.context.variable(self._variable_name_for(operand))] = operand
            self._replace_symbol(symbol, condition_handler, condition_nodes)
        return copied_condition, compared_expressions

    def _replace_symbol(self, symbol: CustomLogicCondition, condition_handler: ConditionHandler, condition_nodes: Set[WorldObject]):
        """
        Replace the given symbol by the corresponding pseudo-condition.

        :symbol: The symbol we want to replace in the custom-logic-condition
        :condition_handler: The object handling the connection between the symbols, the pseudo-logic-condition, and the "real" condition
        :condition_nodes: The set of all nodes in the world that belong to the custom-logic condition where we replace the symbols.
        """
        world_condition = condition_handler.get_z3_condition_of(symbol)._condition
        world_symbol = symbol._condition
        for parent in [parent for parent in self.context.parent_operation(world_symbol) if parent in condition_nodes]:
            for relation in self.context.get_relation(parent, world_symbol):
                index = relation.index
                self.context.remove_operand(parent, relation.sink)
                self.context.add_operand(parent, world_condition, index)

    def serialize(self) -> str:
        """Serialize the given condition into a SMT2 string representation."""
        return self._condition.accept(SerializeVisitor())

    @classmethod
    def deserialize(cls, data: str, context: World) -> LOGICCLASS:
        """Deserialize the given string representing a z3 expression."""
        return CustomLogicCondition(context.from_string(data))

    def rich_string_representation(self, condition_map: Dict[LOGICCLASS, pseudo.Condition]):
        """Replace each symbol by the condition of the condition map and print this condition as string."""
        return self._rich_string_representation(
            self._condition, {symbol._condition: condition for symbol, condition in condition_map.items()}
        )

    # some world-implementation helpers:

    def _is_symbol(self, condition: WorldObject) -> bool:
        return isinstance(condition, Variable) and condition.size == 1 and self.context.get_definition(condition) is None

    def _is_literal(self, condition: WorldObject) -> bool:
        return self._is_symbol(condition) or (isinstance(condition, BitwiseNegate) and self._is_symbol(condition.operand))

    def _is_disjunction_of_literals(self, condition: WorldObject) -> bool:
        """
        Check whether the given condition is a disjunction of literals, i.e., whether it is
            - a symbol,
            - the negation of a symbol or
            - a disjunction of symbols or negation of symbols.
        """
        if self._is_literal(condition):
            return True
        return isinstance(condition, BitwiseOr) and all(self._is_literal(operand) for operand in condition.operands)

    def _get_symbols(self, condition: WorldObject) -> Iterator[Variable]:
        """Get symbols on World-level"""
        for node in self.context.iter_postorder(condition):
            if self._is_symbol(node):
                yield node

    def _get_literals(self, condition: WorldObject) -> Iterator[WorldObject]:
        """Get literals on World-level"""
        if self._is_literal(condition):
            yield condition
        elif isinstance(condition, (BitwiseOr, BitwiseAnd, BitwiseNegate)):
            for child in condition.operands:
                yield from self._get_literals(child)
        else:
            assert isinstance(condition, Constant) and condition.size == 1, f"The condition {condition} does not consist of literals."

    def _rich_string_representation(self, condition: WorldObject, condition_map: Dict[Variable, pseudo.Condition]) -> str:
        """Replace each symbol of the given condition by the pseudo-condition of the condition map and return this condition as string."""
        if self._is_symbol(condition):
            if condition in condition_map:
                return str(condition_map[condition])
            return f"{condition}"
        if isinstance(condition, Constant) and condition.size == 1:
            return "false" if condition.unsigned == 0 else "true"
        if isinstance(condition, BitwiseNegate):
            original_condition = condition.operand
            if original_condition in condition_map:
                return str(condition_map[original_condition].negate())
            return f"!{self._rich_string_representation(original_condition, condition_map)}"
        if isinstance(condition, (BitwiseOr, BitwiseAnd)):
            operands = condition.operands
            symbol = "|" if isinstance(condition, BitwiseOr) else "&"
            if len(operands) == 1:
                return self._rich_string_representation(operands[0], condition_map)
            return "(" + f" {symbol} ".join([f"{self._rich_string_representation(operand, condition_map)}" for operand in operands]) + ")"
        return f"{condition}"

    @staticmethod
    def _variable_name_for(expression: pseudo.Expression) -> str:
        if isinstance(expression, pseudo.Variable):
            return f"{expression},{expression.ssa_name}"
        return f"{expression},{[str(var.ssa_name) for var in expression.requirements]}"

    OPERAND_MAPPING = {
        "==": pseudo.OperationType.equal,
        "!=": pseudo.OperationType.not_equal,
        "s<=": pseudo.OperationType.less_or_equal,
        "u<=": pseudo.OperationType.less_or_equal_us,
        "s>": pseudo.OperationType.greater,
        "u>": pseudo.OperationType.greater_us,
        "s<": pseudo.OperationType.less,
        "u<": pseudo.OperationType.less_us,
        "s>=": pseudo.OperationType.greater_or_equal,
        "u>=": pseudo.OperationType.greater_or_equal_us,
    }


class PseudoCustomLogicCondition(PseudoLogicInterface, CustomLogicCondition, Generic[LOGICCLASS, PseudoLOGICCLASS]):
    def __init__(self, condition: WorldObject, tmp: bool = False):
        super().__init__(condition, tmp)

    @classmethod
    def initialize_from_condition(cls, condition: pseudo.Condition, context: World) -> PseudoLOGICCLASS:
        """Create the simplified condition from the condition of type Condition."""
        custom_condition = cls._get_custom_condition_of(condition, context)
        return cls(custom_condition)

    @classmethod
    def initialize_from_conditions_or(cls, conditions: List[pseudo.Condition], context: World) -> PseudoLOGICCLASS:
        or_conditions = []
        for cond in conditions:
            or_conditions.append(cls._get_custom_condition_of(cond, context))
        return cls(context.bitwise_or(*or_conditions))

    @classmethod
    def initialize_from_formula(cls, condition: LOGICCLASS, condition_map: Dict[LOGICCLASS, PseudoLOGICCLASS]) -> PseudoLOGICCLASS:
        """Create the simplified condition from the condition that is a formula of symbols."""
        condition.to_cnf()
        if condition.is_true:
            return cls.initialize_true(condition.context)
        if condition.is_false:
            return cls.initialize_false(condition.context)
        if condition.is_literal:
            return cls._get_condition_of_literal(condition, condition_map)
        if condition.is_disjunction:
            return cls._get_condition_of_disjunction(condition, condition_map)

        operands = list()
        for conjunction in condition.operands:
            if conjunction.is_literal:
                operands.append(cls._get_condition_of_literal(conjunction, condition_map)._condition)
            else:
                operands.append(cls._get_condition_of_disjunction(conjunction, condition_map)._condition)

        return cls(condition.context.bitwise_and(*operands))

    @classmethod
    def _get_condition_of_disjunction(cls, disjunction: LOGICCLASS, condition_map: Dict[LOGICCLASS, PseudoLOGICCLASS]) -> PseudoLOGICCLASS:
        """Return for a disjunction (Or) the corresponding z3-condition."""
        assert disjunction.is_disjunction, f"The input must be a disjunction, but it is {disjunction}"
        operands = [cls._get_condition_of_literal(operand, condition_map)._condition for operand in disjunction.operands]
        return cls(disjunction.context.bitwise_or(*operands))

    @staticmethod
    def _get_condition_of_literal(literal: LOGICCLASS, condition_map: Dict[LOGICCLASS, PseudoLOGICCLASS]) -> PseudoLOGICCLASS:
        """Given a literal, i.e., a symbol or a negation of a symbol, return the condition the symbol is mapped to."""
        assert literal.is_literal, f"The input must be a literal, but it is {literal}"
        if literal.is_symbol:
            return condition_map[literal]
        return ~condition_map[~literal]

    @staticmethod
    def _get_custom_condition_of(condition: pseudo.Condition, world: World) -> WorldObject:
        """
        Convert a given condition a op b into the custom-condition bit_vec_a op bit_vec_b.

        a and b can be any type of Expression. The name of the bitvector reflects the expression as well as
        the SSA-variable names that occur in the expression.
        """
        if condition.left.type.size != condition.right.type.size:
            logging.warning(
                f"The operands of {condition} have different sizes: {condition.left.type.size} & {condition.right.type.size}. Increase the size of the smaller one."
            )
        bit_vec_size = max(condition.left.type.size, condition.right.type.size, 1)
        operand_1: BitVector = PseudoCustomLogicCondition._convert_expression(condition.left, bit_vec_size, world)
        operand_2: BitVector = PseudoCustomLogicCondition._convert_expression(condition.right, bit_vec_size, world)
        return PseudoCustomLogicCondition.SHORTHAND[condition.operation](world, operand_1, operand_2)

    @staticmethod
    def _convert_expression(expression: pseudo.Expression, bit_vec_size: int, world: World) -> BitVector:
        """Convert the given expression into a z3 bit-vector."""
        if isinstance(expression, pseudo.Constant):
            return world.constant(expression.value, bit_vec_size)
        else:
            return world.variable(PseudoCustomLogicCondition._variable_name_for(expression), bit_vec_size)

    SHORTHAND = {
        pseudo.OperationType.equal: lambda world, a, b: world.bool_equal(a, b),
        pseudo.OperationType.not_equal: lambda world, a, b: world.bool_unequal(a, b),
        pseudo.OperationType.less: lambda world, a, b: world.signed_lt(a, b),
        pseudo.OperationType.less_or_equal: lambda world, a, b: world.signed_le(a, b),
        pseudo.OperationType.greater: lambda world, a, b: world.signed_gt(a, b),
        pseudo.OperationType.greater_or_equal: lambda world, a, b: world.signed_ge(a, b),
        pseudo.OperationType.greater_us: lambda world, a, b: world.unsigned_gt(a, b),
        pseudo.OperationType.less_us: lambda world, a, b: world.unsigned_lt(a, b),
        pseudo.OperationType.greater_or_equal_us: lambda world, a, b: world.unsigned_ge(a, b),
        pseudo.OperationType.less_or_equal_us: lambda world, a, b: world.unsigned_le(a, b),
    }
