# mail_ai_bot.py
from odoo import models, _, fields
from odoo.tools import plaintext2html, html2plaintext
from odoo.exceptions import UserError, ValidationError
import logging
import openai
import datetime

_logger = logging.getLogger(__name__)


class MailBot(models.AbstractModel):
    _name = "mail.ai.bot"
    _description = "Mail AI Bot"

    def _answer_to_message(self, record, values, ai_bot_user=None):
        author_id = values.get("author_id")
        channel_type = record.channel_type

        if len(record) != 1 or values.get("message_type") != "comment":
            return

        if channel_type == "chat":
            if self._is_bot_in_private_channel(record):
                if values.get("body", "").startswith("!"):
                    answer_type = "important"
                else:
                    answer_type = "chat"

                try:
                    answer = self._get_answer(
                        record, answer_type, ai_bot_user=ai_bot_user
                    )
                except openai.APIError as err:
                    _logger.error(err)
                    if "maximum context length" in err.message:
                        answer = _(
                            "ERROR - Sorry, this request requires too many tokens."
                            'Please consider using the command "\\\\\\\\clean" to clear the AI chat.'
                        )
                    else:
                        raise UserError(err.message)

                if answer:
                    message_type = "comment"
                    subtype_id = self.env["ir.model.data"]._xmlid_to_res_id(
                        "mail.mt_comment"
                    )
                    record = record.with_context(mail_create_nosubscribe=True).sudo()

                    if ai_bot_user:
                        author_id = ai_bot_user.partner_id.id
                    else:
                        ai_bot_user = (
                            self.env["res.users"]
                            .sudo()
                            .search([("is_ai_bot", "=", True)], limit=1)
                        )
                        if ai_bot_user:
                            author_id = ai_bot_user.partner_id.id
                        else:
                            _logger.warning("No AI bot user found.")
                            return None

                    record.message_post(
                        body=answer,
                        author_id=author_id,
                        message_type=message_type,
                        subtype_id=subtype_id,
                    )

        elif channel_type in ["group", "channel"]:
            ai_bot_users = (
                self.env["res.users"]
                .sudo()
                .search(
                    [
                        ("partner_id", "in", record.channel_member_ids.partner_id.ids),
                        ("is_ai_bot", "=", True),
                    ]
                )
            )

            if ai_bot_users:
                # Encontrar a última menção de qualquer usuário na conversa
                last_mention = (
                    record.message_ids.filtered(lambda m: m.partner_ids)
                    .sudo()
                    .sorted("date", reverse=True)[:1]
                )

                # Encontrar o usuário que foi mencionado por último (bot ou humano)
                mentioned_user = None
                if last_mention and last_mention.partner_ids:
                    mentioned_user = last_mention.partner_ids[0].user_ids.sudo()[:1]

                # Lógica dos 5 minutos usando a data da última menção
                five_minutes_ago = fields.Datetime.now() - datetime.timedelta(minutes=5)

                # Verifica se o bot deve responder
                should_respond = False
                if mentioned_user and mentioned_user.is_ai_bot:
                    # Se um bot foi mencionado, ele deve responder
                    should_respond = True
                    ai_bot_user = mentioned_user  # Define o bot que deve responder como o mencionado
                elif (
                    last_mention
                    and last_mention.date >= five_minutes_ago
                    and last_mention.author_id.user_ids.sudo().exists()
                    and last_mention.author_id.user_ids.sudo()[0].is_ai_bot
                ):
                    # Se um bot respondeu recentemente e ninguém mais falou, ele continua respondendo
                    should_respond = True
                    ai_bot_user = last_mention.author_id.user_ids.sudo()[
                        0
                    ]  # Define o bot que deve responder como o último que respondeu

                if should_respond:
                    if values.get("body", "").startswith("!"):
                        answer_type = "important"
                    else:
                        answer_type = "chat"

                    try:
                        answer = self._get_answer(
                            record,
                            answer_type,
                            ai_bot_user=ai_bot_user,
                        )
                    except openai.APIError as err:
                        _logger.error(err)
                        if "maximum context length" in err.message:
                            answer = _(
                                "ERROR - Sorry, this request requires too many tokens."
                                'Please consider using the command "\\\\\\\\clean" to clear the AI chat.'
                            )
                        else:
                            raise UserError(err.message)

                    if answer:
                        message_type = "comment"
                        subtype_id = self.env["ir.model.data"]._xmlid_to_res_id(
                            "mail.mt_comment"
                        )

                        if (
                            author_id
                            and self.env["res.users"].sudo().browse(author_id).is_ai_bot
                        ):
                            return

                        record = record.with_context(
                            mail_create_nosubscribe=True
                        ).sudo()

                        record.message_post(
                            body=answer,
                            author_id=ai_bot_user.partner_id.id,
                            message_type=message_type,
                            subtype_id=subtype_id,
                        )

    def get_chat_messages(self, record, header, only_human=False, ai_bot_user=None):
        # Encontra o usuário do bot se não for fornecido
        if ai_bot_user is None:
            ai_bot_user = (
                self.env["res.users"].sudo().search([("is_ai_bot", "=", True)], limit=1)
            )

        if not ai_bot_user:
            _logger.warning("Nenhum usuário de bot de IA encontrado.")
            return []

        # Filtra apenas mensagens com conteúdo e que não sejam notificações do sistema
        previous_message_ids = (
            record.message_ids.sudo()
            .filtered(lambda m: m.body != "" and m.message_type != "notification")
            .sorted("date")
        )

        if only_human:
            # Usa ai_bot_user para verificar se a mensagem é do bot
            previous_message_ids = previous_message_ids.filtered(
                lambda m: m.author_id != ai_bot_user.partner_id
            )

        # Pega a última mensagem enviada por um humano
        last_human_message = previous_message_ids.filtered(
            lambda m: m.author_id != ai_bot_user.partner_id
        )[-1:]

        if last_human_message:
            partner_name = last_human_message.author_id.sudo().name
            header = f"the person you are chatting with is named {partner_name}. follow: {header}"

        chat_messages = [{"role": "system", "content": header}] if header else []
        for message_id in previous_message_ids:
            role = (
                "assistant"
                if message_id.author_id == ai_bot_user.partner_id
                else "user"
            )

            content = html2plaintext(message_id.body)

            # Keep only the first part of the message if it's a group/channel message
            if record.channel_type in ["group", "channel"]:
                content = content.splitlines()[0]  # Get the first line
                content = content.replace("[1]", "").strip()

            chat_message = {"role": role, "content": content}
            chat_messages.append(chat_message)

        return chat_messages

    def _get_answer(self, record, answer_type="chat", ai_bot_user=None):

        # Use the provided ai_bot_user if available
        if ai_bot_user is None:
            ai_bot_user = (
                self.env["res.users"]
                .sudo()
                .search(
                    [
                        ("partner_id", "=", record.ai_bot_partner_id.id),
                        ("is_ai_bot", "=", True),
                    ],
                    limit=1,
                )
            )

        if not ai_bot_user:
            _logger.warning("No AI bot user found in this channel.")
            return None

        completion_id = ai_bot_user.chat_completion_id

        if not completion_id:
            raise ValidationError(
                _("Chat Completion not configured for the AI bot in this channel.")
            )

        header = completion_id.prompt_template

        if answer_type == "chat":
            messages = self.get_chat_messages(record, header, ai_bot_user=ai_bot_user)
            _logger.info("Mensagens enviadas para o OpenAI: %s", messages)
            res = completion_id.create_completion(messages=messages)
        elif answer_type == "important":
            messages = self.get_chat_messages(
                record, header, only_human=True, ai_bot_user=ai_bot_user
            )
            res = completion_id.create_completion(messages, max_tokens=2048)
        else:
            return None

        if res:
            return res[0]
        else:
            return None

    def _is_bot_in_private_channel(self, record):
        if record._name == "discuss.channel" and record.channel_type == "chat":
            return any(
                member.partner_id.user_ids
                and any(user.is_ai_bot for user in member.partner_id.user_ids)
                for member in record.with_context(active_test=False)
                .sudo()
                .channel_member_ids
            )
        return False
