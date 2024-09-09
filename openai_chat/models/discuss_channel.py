# discuss_channel.py (com atualizações)
from odoo import models, fields, api, _
import logging
import datetime

_logger = logging.getLogger(__name__)


class Channel(models.Model):
    _inherit = "discuss.channel"

    ai_bot_partner_id = fields.Many2one(
        "res.partner", compute="_compute_ai_bot_partner", store=False
    )

    @api.depends(
        "channel_member_ids.partner_id.user_ids.is_ai_bot",
        "message_ids.author_id.user_ids.is_ai_bot",
    )
    def _compute_ai_bot_partner(self):
        for rec in self:
            # Find the AI bot partner based on channel members
            # Adicione sudo() se houver restrições de acesso em res.partner
            ai_bot_partner = rec.channel_member_ids.sudo().partner_id.filtered(
                lambda p: p.user_ids and p.user_ids.is_ai_bot
            )
            rec.ai_bot_partner_id = ai_bot_partner[:1]  # Limit to one

    def execute_command_clear_ai_chat(self, **kwargs):
        partner = self.env.user.partner_id
        key = kwargs["body"]
        if key.lower().strip() == "/clear":
            if self.ai_bot_partner_id:
                self.env["bus.bus"]._sendone(
                    self.env.user.partner_id,
                    "mail.message/delete",
                    {"message_ids": self.message_ids.ids},
                )
                self.message_ids.sudo().unlink()

    @api.returns("mail.message", lambda value: value.id)
    def message_post(self, **kwargs):
        # Store the original message body without modifications
        original_body = kwargs.get("body", "")

        message = super(Channel, self).message_post(**kwargs)
        _logger.info("Channel Type: %s", self.channel_type)
        #  Prevent the bot's response from triggering itself
        if message.author_id.user_ids and message.author_id.user_ids.is_ai_bot:
            return message

        # Verifica se a mensagem é do próprio bot APENAS em canais de grupo/canal
        if (
            self.channel_type in ["group", "channel"]
            and message.author_id.user_id.is_ai_bot
        ):
            return message  # Ignora a mensagem se for do próprio bot em um canal de grupo/canal

        # Find the relevant AI bot user based on the channel and sender
        ai_bot_user = self._get_ai_bot_user_for_message(message)
        _logger.info("AI Bot: %s", ai_bot_user)
        if ai_bot_user:
            if message.author_id != ai_bot_user.partner_id:
                msg_vals = {
                    key: val
                    for key, val in kwargs.items()
                    if key in self.env["mail.message"]._fields
                }
                # Use the original body
                msg_vals["body"] = original_body

                # Avoid infinite loop
                if not self.env.context.get("from_ai_bot"):
                    self.env.cr.commit()  # Forçar a escrita no banco de dados - flush()
                    self._invalidate_cache()
                    self.env["mail.ai.bot"].with_context(
                        from_ai_bot=True
                    )._answer_to_message(self, msg_vals, ai_bot_user)
        return message

    def _get_ai_bot_user_for_message(self, message):
        """
        Determines the relevant AI bot user for the given message.

        :param message: The mail.message record.
        :return: The res.users record of the AI bot, or None if no relevant bot is found.
        """

        # Check if it's a private chat with an AI bot
        if self.channel_type == "chat" and self.ai_bot_partner_id:
            if message.author_id != self.ai_bot_partner_id:
                return (
                    self.env["res.users"]
                    .sudo()
                    .search(
                        [
                            ("partner_id", "=", self.ai_bot_partner_id.id),
                            ("is_ai_bot", "=", True),
                        ],
                        limit=1,
                    )
                )
        # Check if it's a group/channel chat and the bot was mentioned
        elif self.channel_type == "livechat":
            ai_bot_user = (
                self.env["res.users"]
                .sudo()
                .search(
                    [
                        (
                            "partner_id",
                            "in",
                            self.channel_member_ids.partner_id.ids,
                        ),
                        ("is_ai_bot", "=", True),
                    ],
                    limit=1,
                )
            )
            return ai_bot_user
        # Check if it's a group/channel chat and the bot was mentioned
        elif self.channel_type in ["group", "channel"]:
            # Check for direct mentions
            mentioned_partner_ids = message.partner_ids.filtered(
                lambda p: p.user_ids and p.user_ids.is_ai_bot
            )

            if mentioned_partner_ids:
                return mentioned_partner_ids.user_ids[0]
            else:
                # If no bot was mentioned, use any bot in the channel
                ai_bot_user = (
                    self.env["res.users"]
                    .sudo()
                    .search(
                        [
                            (
                                "partner_id",
                                "in",
                                self.channel_member_ids.partner_id.ids,
                            ),
                            ("is_ai_bot", "=", True),
                        ],
                        limit=1,
                    )
                )
                return ai_bot_user

        # No relevant AI bot found
        return None
