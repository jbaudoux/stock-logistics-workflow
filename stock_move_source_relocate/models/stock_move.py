# Copyright 2020 Camptocamp SA
# Copyright 2023 Michael Tietz (MT Software) <mtietz@mt-software.de>
# Copyright 2023 Jacques-Etienne Baudoux (BCIM) <je@bcim.be>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl)
import logging

from odoo import models
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_assign(self, force_qty=False):
        unasigned_moves = self.filtered(
            lambda m: m.state in ["confirmed", "partially_available"]
        )
        result = super()._action_assign(force_qty=force_qty)
        # could not be (entirely) reserved
        unasigned_moves = unasigned_moves.filtered(
            lambda m: m.state in ["confirmed", "partially_available"]
        )
        if unasigned_moves:
            unasigned_moves._apply_source_relocate()
        return result

    def _apply_source_relocate(self):
        """Apply relocation rules."""
        relocated_moves = self.browse()
        for move in self:
            if move.picked or move.picking_id.printed:
                continue
            # We don't need to ignore moves with "_should_bypass_reservation()
            # is True" because they are reserved at this point.
            relocation = self.env["stock.source.relocate"]._rule_for_move(move)
            if not relocation or relocation.relocate_location_id == move.location_id:
                continue
            relocated = move._apply_source_relocate_rule(relocation)
            if relocated:
                relocated_moves |= relocated
        if relocated_moves:
            _logger.debug(f"Relocated moves {relocated_moves}")
            relocated_moves._after_apply_source_relocate_rule()

    def _apply_source_relocate_rule(self, relocation):
        """Perform the source location relocation.

        Returns the relocated move.
        """
        self.ensure_one()
        _logger.debug(
            f"Relocate {self} of operation type {self.picking_type_id.name} "
            f"to source location {relocation.relocate_location_id.display_name}"
        )
        rounding = self.product_uom.rounding
        qty_reserved = self.quantity

        if float_compare(qty_reserved, 0, precision_rounding=rounding) == 0:
            # Nothing is reserved, modify the move
            self.location_id = relocation.relocate_location_id
            # Do not call _action_confirm on a split moves inside _action_assign
            return self

        missing_reserved_uom_quantity = self.product_uom_qty - qty_reserved
        need = self.product_uom._compute_quantity(
            missing_reserved_uom_quantity,
            self.product_id.uom_id,
            rounding_method="HALF-UP",
        )
        if float_compare(need, 0, precision_rounding=rounding) <= 0:
            # Everything is reserved (shouldn't happen), do nothing
            return self.env["stock.move"].browse()

        # A part of the quantity could be reserved in the original
        # location, so keep this part in the move and split the rest
        # in a new move, where will take the goods in the relocation
        move_vals_list = self._split(need)
        for move_vals in move_vals_list:
            move_vals["location_id"] = relocation.relocate_location_id.id
            # Do not call _action_confirm on a split moves inside _action_assign
            move_vals["state"] = "confirmed"
            move_vals["reservation_date"] = self.reservation_date
        return self.create(move_vals_list)

    def _after_apply_source_relocate_rule(self):
        for moves in self.grouped("picking_id").values():
            moves._merge_moves()
