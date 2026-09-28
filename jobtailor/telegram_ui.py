"""Optional setup panel. No Streamlit objects are passed to workers."""

import streamlit as st

from .telegram_notifications import TelegramError, begin_pairing, finish_pairing


def render_telegram_settings(notifications):
    with st.expander("Telegram notifications (optional)", expanded=False):
        status = notifications.snapshot()
        if status["connected"]:
            st.success(
                f"Connected to {status['name']} through @{status['bot_username']}"
            )
            enabled = st.toggle(
                "Notify me after each job finishes",
                value=status["enabled"],
                key="telegram_enabled_v012",
            )
            notifications.set_enabled(enabled)
            st.caption(
                "Applies to jobs finishing from now on. Cancelled jobs do not send alerts."
            )
            if st.button("Disconnect Telegram", key="telegram_disconnect_v012"):
                notifications.disconnect()
                st.session_state.pop("telegram_enabled_v012", None)
                st.session_state.pop("telegram_pairing_v012", None)
                st.session_state.pop("telegram_token_v012", None)
                st.rerun()
        else:
            st.write(
                "Get a message with the role, company, application link, folder path, and review status."
            )
            st.markdown(
                "1. Open [BotFather](https://t.me/BotFather) in Telegram, send `/newbot`, "
                "and follow its prompts to create a bot for this app.\n"
                "2. Paste its bot token below and click **Connect Telegram**.\n"
                "3. Open the connection link, press **Start** in Telegram, then click **Check connection** here."
            )
            st.caption(
                "Telegram bots cannot address a private chat by phone number. The connection link finds your chat automatically."
            )
            token = st.text_input(
                "Bot token", type="password", key="telegram_token_v012"
            )
            if st.button(
                "Connect Telegram",
                disabled=not token.strip(),
                key="telegram_connect_v012",
            ):
                st.session_state.pop("telegram_pairing_v012", None)
                try:
                    with st.spinner("Checking your bot..."):
                        st.session_state.telegram_pairing_v012 = begin_pairing(token)
                except TelegramError as exc:
                    st.error(str(exc))
            pairing = st.session_state.get("telegram_pairing_v012")
            if pairing:
                st.link_button("Open Telegram and press Start", pairing.url)
                st.caption(
                    "Use the Telegram account where you want to receive alerts. This link expires in 10 minutes."
                )
                if st.button("Check connection", key="telegram_check_v012"):
                    try:
                        with st.spinner("Looking for your Start message..."):
                            target = finish_pairing(pairing)
                        if target:
                            notifications.connect(target)
                            st.session_state.pop("telegram_pairing_v012", None)
                            st.session_state.pop("telegram_enabled_v012", None)
                            st.rerun()
                        else:
                            st.info(
                                "No connection message yet. Open the link above, press Start, and check again."
                            )
                    except TelegramError as exc:
                        st.error(str(exc))
        st.caption(
            "Keep the app running to send alerts. The connection lasts for this browser session; "
            "a new session or app restart needs reconnecting. Your bot token is kept in memory. "
            "Messages include the folder path on your computer; that path will not open the folder on your phone."
        )
