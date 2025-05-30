import logging
import os
from functools import wraps

# Get environment variables
admin = os.getenv("ADMIN", "")

# Command registry
admin_commands = {}
COMMAND_PREFIX = "fae;"  # Should match the one in main file


def admin_command(command_name):
    """Decorator to register admin commands with secure error handling"""

    def decorator(func):
        @wraps(func)
        async def wrapper(bot, message, *args, **kwargs):
            try:
                # Check if user is admin
                if message.author.name not in [
                    name.strip() for name in admin.split(",")
                ]:
                    logging.info(
                        f"Admin command attempted whilst not admin by {message.author.name}"
                    )
                    return await message.channel.send(
                        "You must be admin to use these commands"
                    )

                # Execute the command
                return await func(bot, message, *args, **kwargs)

            except Exception as e:
                # Log the detailed error
                logging.error(
                    f"Error executing command '{command_name}': {str(e)}", exc_info=True
                )

                # Provide sanitized feedback in the channel
                await message.channel.send(
                    "Command execution failed. Check logs for details."
                )

        # Register the command
        admin_commands[COMMAND_PREFIX + command_name] = wrapper
        return wrapper

    return decorator


# Admin command implementations
@admin_command("conversations")
async def _list_conversations(bot, message, message_tokens=None, conversation_id=None):
    """List all conversations in memory"""
    if len(bot.conversations) == 0:
        logging.info("asked to list conversations but there are none")
        return await message.channel.send("there are no conversations in memory")
    # Create a formatted string of conversations
    reply = "here are the conversations I have in memory:\n"
    for x in bot.conversations.keys():
        reply = (
            reply
            + str(bot.conversations[x]["id"])
            + " - "
            + str(bot.conversations[x]["name"])
            + " - "
            + str(len(bot.conversations[x]["conversation"]))
            + "\n"
        )
    logging.info(
        f"Admin {message.author.name} listed {len(bot.conversations)} conversations"
    )
    return await message.channel.send(reply)


@admin_command("invite")
async def _invite_conversation(bot, message, message_tokens=None, conversation_id=None):
    """initialises a conversation in a non dm channel"""
    logging.info(
        f"Admin {message.author.name} initialized new conversation in channel with ID {conversation_id}"
    )
    return await bot._initialize_conversation(
        message, message_tokens=message_tokens, conversation_id=conversation_id
    )


@admin_command("forget")
async def _forget_conversation(bot, message, message_tokens, conversation_id):
    """Forget a conversation by clearing its memory"""
    # Check if there are conversations to forget
    if len(bot.conversations) == 0:
        logging.info(
            f"asked to clear memory, but there are no conversations. Message was {message.content}"
        )
        return await message.channel.send("there are no conversations to forget")

    # Determine which conversation to forget
    to_forget = None

    # If no conversation ID provided, use current
    if len(message_tokens) < 2:
        to_forget = conversation_id
        logging.info(
            f"asked to forget without providing a conversation id, using current conversation {conversation_id}"
        )
    # If ID provided, validate it
    else:
        provided_id = message_tokens[1]
        if provided_id in bot.conversations:
            to_forget = provided_id
        else:
            logging.info(
                f"asked to forget conversation {provided_id}, but it does not exist. Message was {message.content}"
            )
            return await message.channel.send(
                f"Conversation ID '{provided_id}' does not exist. Please provide a valid conversation ID."
            )

    # Clear the conversation from memory
    bot.conversation = []
    bot.conversations[to_forget]["conversation"] = bot.conversation

    logging.info(
        f"Admin {message.author.name} cleared memory for conversation {to_forget}"
    )
    return await message.channel.send(f"cleared conversation {to_forget}")


@admin_command("help")
async def _admin_help(bot, message, message_tokens=None, conversation_id=None):
    """Show available admin commands"""
    commands = list(admin_commands.keys())
    help_text = "Available admin commands:\n"
    for cmd in commands:
        doc = admin_commands[cmd].__doc__ or "No description"
        help_text += f"- `{cmd}`: {doc}\n"
    return await message.channel.send(help_text)


@admin_command("model")
async def _set_or_return_model(bot, message, message_tokens=None, conversation_id=None):
    """sets the model to use for generating responses or returns the current model name. Usage: fae;model [conversation_id] [new_model]"""
    target_id = conversation_id

    # Check if first argument is a conversation ID
    if len(message_tokens) > 1:
        potential_conv_id = message_tokens[1]
        if potential_conv_id in bot.conversations:
            target_id = potential_conv_id
            message_tokens = [message_tokens[0]] + message_tokens[2:]
        elif potential_conv_id.isdigit():
            logging.debug(
                f"Admin {message.author.name} attempted to access non-existent conversation {potential_conv_id}"
            )
            return await message.channel.send(
                f"Conversation {potential_conv_id} not found"
            )

    if len(message_tokens) > 1:
        new_model = message_tokens[1]
        old_model = bot.conversations[target_id]["model"]
        bot.conversations[target_id]["model"] = new_model
        logging.debug(
            f"Admin {message.author.name} changed model for conversation {target_id} from {old_model} to {new_model}"
        )
        return await message.channel.send(
            f"Model changed to: {new_model} for conversation {target_id}"
        )
    else:
        current_model = bot.conversations[target_id]["model"]
        logging.debug(
            f"Admin {message.author.name} queried model for conversation {target_id}: {current_model}"
        )
        return await message.channel.send(
            f"Current model for conversation {target_id}: {current_model}"
        )


@admin_command("frequency")
async def _set_reply_frequency(bot, message, message_tokens, conversation_id):
    """Set or get reply frequency (0-1) for a conversation. Usage: fae;frequency [conversation_id] [value]"""
    target_id = conversation_id

    # Check if first argument is a conversation ID
    if len(message_tokens) > 1:
        potential_conv_id = message_tokens[1]
        if potential_conv_id in bot.conversations:
            target_id = potential_conv_id
            message_tokens = [message_tokens[0]] + message_tokens[2:]

    if len(message_tokens) > 1:
        try:
            new_freq = float(message_tokens[1])
            if not 0 <= new_freq <= 1:
                return await message.channel.send("Frequency must be between 0 and 1")
            old_freq = bot.conversations[target_id]["reply_frequency"]
            bot.conversations[target_id]["reply_frequency"] = new_freq
            logging.debug(
                f"Admin {message.author.name} changed reply frequency for conversation {target_id} from {old_freq} to {new_freq}"
            )
            return await message.channel.send(
                f"Reply frequency set to: {new_freq} for conversation {target_id}"
            )
        except ValueError:
            logging.debug(
                f"Admin {message.author.name} provided invalid frequency value: {message_tokens[1]}"
            )
            return await message.channel.send(
                "Please provide a valid number between 0 and 1"
            )
    else:
        current_freq = bot.conversations[target_id]["reply_frequency"]
        logging.debug(
            f"Admin {message.author.name} queried reply frequency for conversation {target_id}: {current_freq}"
        )
        return await message.channel.send(
            f"Current reply frequency for conversation {target_id}: {current_freq}"
        )


@admin_command("history")
async def _set_history_length(bot, message, message_tokens, conversation_id):
    """Set or get maximum history length for a conversation. Usage: fae;history [conversation_id] [length]"""
    target_id = conversation_id

    # Check if first argument is a conversation ID
    if len(message_tokens) > 1:
        potential_conv_id = message_tokens[1]
        if potential_conv_id in bot.conversations:
            target_id = potential_conv_id
            message_tokens = [message_tokens[0]] + message_tokens[2:]
        elif potential_conv_id.isdigit():
            logging.debug(
                f"Admin {message.author.name} attempted to access non-existent conversation {potential_conv_id}"
            )
            return await message.channel.send(
                f"Conversation {potential_conv_id} not found"
            )

    if len(message_tokens) > 1:
        try:
            new_length = int(message_tokens[1])
            if new_length < 1:
                return await message.channel.send("History length must be positive")
            old_length = bot.conversations[target_id]["history_length"]
            bot.conversations[target_id]["history_length"] = new_length
            logging.debug(
                f"Admin {message.author.name} changed history length for conversation {target_id} from {old_length} to {new_length}"
            )
            return await message.channel.send(
                f"History length set to: {new_length} for conversation {target_id}"
            )
        except ValueError:
            logging.debug(
                f"Admin {message.author.name} provided invalid history length: {message_tokens[1]}"
            )
            return await message.channel.send("Please provide a valid positive integer")
    else:
        current_length = bot.conversations[target_id]["history_length"]
        logging.debug(
            f"Admin {message.author.name} queried history length for conversation {target_id}: {current_length}"
        )
        return await message.channel.send(
            f"Current history length for conversation {target_id}: {current_length}"
        )


@admin_command("prompt")
async def _set_conversation_prompt(
    bot, message, message_tokens=None, conversation_id=None
):
    """Set or get the prompt for a conversation. Usage: fae;prompt [conversation_id] [new_prompt]

    Supports placeholders:
    {server} - Server name
    {channel} - Channel name
    {topic} - Channel topic
    {author} - Message author name
    """
    target_id = conversation_id

    # Check if first argument is a conversation ID
    if len(message_tokens) > 1:
        potential_conv_id = message_tokens[1]
        if potential_conv_id in bot.conversations:
            target_id = potential_conv_id
            message_tokens = [message_tokens[0]] + message_tokens[2:]
        elif potential_conv_id.isdigit():
            logging.debug(
                f"Admin {message.author.name} attempted to access non-existent conversation {potential_conv_id}"
            )
            return await message.channel.send(
                f"Conversation {potential_conv_id} not found"
            )

    if len(message_tokens) > 1:
        new_prompt = " ".join(message_tokens[1:])

        # Replace placeholders with actual values
        conversation_data = bot.conversations[target_id]
        new_prompt = new_prompt.replace(
            "{server}", conversation_data.get("server_name", "")
        )
        new_prompt = new_prompt.replace(
            "{channel}", conversation_data.get("channel_name", "")
        )
        new_prompt = new_prompt.replace(
            "{topic}", conversation_data.get("channel_topic", "")
        )
        new_prompt = new_prompt.replace(
            "{conversants}", ", ".join(conversation_data.get("conversants", ""))
        )

        bot.conversations[target_id]["prompt"] = new_prompt
        prompt_preview = (
            (new_prompt[:75] + "...") if len(new_prompt) > 75 else new_prompt
        )
        logging.debug(
            f"Admin {message.author.name} changed prompt for conversation {target_id} to: {prompt_preview}"
        )
        return await message.channel.send(
            f"Prompt set for conversation {target_id}: {prompt_preview}"
        )
    else:
        current_prompt = bot.conversations[target_id]["prompt"]
        prompt_preview = (
            (current_prompt[:75] + "...")
            if len(current_prompt) > 75
            else current_prompt
        )
        logging.debug(
            f"Admin {message.author.name} queried prompt for conversation {target_id}"
        )
        return await message.channel.send(
            f"Current prompt for conversation {target_id}: {current_prompt}"
        )


@admin_command("debug")
async def _toggle_debug_mode(bot, message, message_tokens=None, conversation_id=None):
    """Toggle debug mode on or off. Debug mode shows prompts in logs. Usage: fae;debug"""
    # Toggle the debug state on the bot instance
    bot.debug_prompts = not bot.debug_prompts
    status = "on" if bot.debug_prompts else "off"

    logging.info(f"Admin {message.author.name} set debug mode to: {status}")
    return await message.channel.send(f"Debug mode is now: {status}")
