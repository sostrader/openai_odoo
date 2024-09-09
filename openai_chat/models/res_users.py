# res_users.py
from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = "res.users"

    chat_completion_id = fields.Many2one("openai.completion", string="Chat Completion")
    is_ai_bot = fields.Boolean(string="Is AI Bot")
    status = fields.Selection(
        selection=[
            ("done", "Online"),
            ("blocked", "Offline"),
        ],
        string="Login Status",
        default="blocked",
        readonly=True,
    )
    total_log_record = fields.Integer(
        "Total Log Information", compute="_count_total_log"
    )
    im_status = fields.Char(string="IM Status", compute="_compute_im_status")

    @api.depends("partner_id.im_status")
    def _compute_im_status(self):
        for user in self:
            user.im_status = user.partner_id.im_status

    def _init_messaging(self):
        if self.sudo()._is_internal():
            self.sudo()._init_ai_bot()
        return super()._init_messaging()

    def _init_ai_bot(self):
        self.ensure_one()
        if self.is_ai_bot:
            # Encontra o partner do AI Bot com base no campo is_ai_bot
            ai_bot_partner = (
                self.env["res.partner"]
                .sudo()
                .search([("user_ids.is_ai_bot", "=", True)], limit=1)
            )
            if ai_bot_partner:
                channel_info = (
                    self.env["mail.channel"]
                    .sudo()
                    .channel_get([ai_bot_partner.id, self.partner_id.id])
                )
                channel = self.env["mail.channel"].sudo().browse(channel_info["id"])
                return channel

    def _count_total_log(self):
        for rec in self:
            total_log = (
                self.env["res.users.log"]
                .sudo()
                .search_count([("create_uid", "=", rec.id)])
            )
            rec.total_log_record = total_log


class Partner(models.Model):
    _inherit = "res.partner"

    @api.depends("user_ids.is_ai_bot")
    def _compute_im_status(self):
        super(Partner, self)._compute_im_status()
        for partner in self:
            if partner.user_ids and partner.user_ids[0].is_ai_bot:
                # Define o status do Partner e do Usuário como online de forma síncrona, com sudo
                partner.sudo().write({"im_status": "online"})
                partner.user_ids[0].sudo().write({"status": "done"})

    ai_bot_id = fields.Char(string="AI Bot ID", compute="_compute_ai_bot_id")

    @api.depends("user_ids.is_ai_bot")
    def _compute_ai_bot_id(self):
        for partner in self:
            ai_bot_user = partner.user_ids.filtered("is_ai_bot")
            if ai_bot_user:
                partner.ai_bot_id = str(ai_bot_user.id)  # Use user ID as the AI Bot ID
            else:
                partner.ai_bot_id = False
