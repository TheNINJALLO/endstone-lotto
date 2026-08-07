"""Main lotto plugin implementation."""
import time
import random
from pathlib import Path
from typing import Dict, Any, List

from endstone.plugin import Plugin
from endstone.event import event_handler, PlayerInteractEvent, PlayerJoinEvent, EventPriority
from endstone.command import Command, CommandSender
from endstone.form import ActionForm, ModalForm, TextInput, MessageForm
from endstone import Player

from .config import LottoConfig


class LottoPlugin(Plugin):
    """A lottery system plugin with configurable prizes and scheduled drawings."""

    api_version = "0.11"
    MONEY_OBJECTIVE = "Money"
    
    # Check every 5 minutes (5 * 60 * 20 ticks) instead of scheduling massive delays
    CHECK_INTERVAL_TICKS = 6000  # 5 minutes at 20 TPS

    commands = {
        "lotto": {
            "description": "Open the lottery admin menu",
            "usages": ["/lotto"],
            "permissions": ["lotto.admin"],
        },
        "lottoreload": {
            "description": "Reload the lotto configuration",
            "usages": ["/lottoreload"],
            "permissions": ["lotto.admin"],
        }
    }

    permissions = {
        "lotto.admin": {
            "description": "Allow users to use lotto admin commands",
            "default": "op",
        }
    }

    def __init__(self):
        """Initialize the plugin."""
        super().__init__()
        self.lotto_config: LottoConfig = None
        self.check_task_id: int = None  # Single periodic checker task
        self.interaction_cooldowns: Dict[str, float] = {}  # player_name -> last_interaction_time

    def on_enable(self) -> None:
        """Called when the plugin is enabled."""
        self.logger.info("=" * 50)
        self.logger.info("LOTTO PLUGIN STARTING...")
        self.logger.info("=" * 50)

        # Initialize config
        config_path = Path(self.data_folder) / "config.json"
        self.logger.info(f"Config path: {config_path}")
        self.lotto_config = LottoConfig(config_path)
        self.logger.info(f"Interaction block set to: {self.lotto_config.get_interaction_block()}")

        # Register event handlers
        self.logger.info("Registering event handlers...")
        self.register_events(self)
        self.logger.info("Event handlers registered!")

        # Start the periodic draw checker
        self.schedule_draw_checker()

        self.logger.info("=" * 50)
        self.logger.info("LOTTO PLUGIN ENABLED SUCCESSFULLY!")
        self.logger.info("Draw checker running every 5 minutes")
        self.logger.info("=" * 50)

    def on_disable(self) -> None:
        """Called when the plugin is disabled."""
        # Cancel the checker task
        if self.check_task_id is not None:
            try:
                self.server.scheduler.cancel_task(self.check_task_id)
            except Exception:
                pass

        self.logger.info("Lotto plugin disabled!")

    def on_command(self, sender: CommandSender, command: Command, args: List[str]) -> bool:
        """Handle plugin commands.

        Args:
            sender: The command sender
            command: The command object
            args: Command arguments

        Returns:
            True if command was handled
        """
        if command.name == "lotto":
            if not isinstance(sender, Player):
                sender.send_error_message("This command can only be used by players.")
                return True

            if not command.test_permission_silently(sender):
                sender.send_error_message("You don't have permission to use this command.")
                return True

            self.show_admin_menu(sender)
            return True

        elif command.name == "lottoreload":
            if not command.test_permission_silently(sender):
                sender.send_error_message("You don't have permission to use this command.")
                return True

            # Cancel existing checker task
            if self.check_task_id is not None:
                try:
                    self.server.scheduler.cancel_task(self.check_task_id)
                except Exception:
                    pass
                self.check_task_id = None

            # Reload config
            self.lotto_config.load()

            # Restart checker
            self.schedule_draw_checker()

            sender.send_message("§aLotto configuration reloaded successfully!")
            return True

        return False

    @event_handler(priority=EventPriority.NORMAL, ignore_cancelled=False)
    def on_player_interact(self, event: PlayerInteractEvent) -> None:
        """Handle player block interaction events.

        Args:
            event: The interaction event
        """
        # Only respond to right-click on blocks to avoid duplicate triggers
        if event.action != PlayerInteractEvent.Action.RIGHT_CLICK_BLOCK:
            return

        if not event.has_block:
            return

        block = event.block
        configured_block = self.lotto_config.get_interaction_block()

        if block.type != configured_block:
            return

        player = event.player
        current_time = time.time()

        # Check cooldown (500ms to prevent multiple menus from one click)
        if player.name in self.interaction_cooldowns:
            last_interaction = self.interaction_cooldowns[player.name]
            if current_time - last_interaction < 0.5:
                return

        # Update cooldown
        self.interaction_cooldowns[player.name] = current_time

        # Cancel the event to prevent default block interaction
        event.is_cancelled = True

        # Always show player menu when interacting with the block
        # (OPs use the /lotto command for admin functions)
        self.show_player_menu(player)

    @event_handler(priority=EventPriority.NORMAL, ignore_cancelled=False)
    def on_player_join(self, event: PlayerJoinEvent) -> None:
        """Handle player join events to give pending rewards.

        Args:
            event: The join event
        """
        player = event.player
        self.logger.info(f"=== PLAYER JOIN EVENT FIRED for {player.name} ===")
        pending = list(self.lotto_config.get_pending_rewards(player.name))

        if pending:
            remaining = []
            for reward in pending:
                amount = reward.get("amount")
                lotto_id = reward.get("lotto_id")
                if not isinstance(amount, int) or amount <= 0 or not isinstance(lotto_id, str):
                    self.logger.error(f"Invalid pending lotto reward for {player.name}: {reward!r}")
                    remaining.append(reward)
                    continue

                if self.give_money(player, amount):
                    lotto_type = self.lotto_config.get_lotto_type(lotto_id)
                    display_name = lotto_type["display_name"] if lotto_type else lotto_id
                    player.send_message(
                        f"§a§lYou won ${amount:,} from the {display_name} lotto while you were offline!"
                    )
                else:
                    remaining.append(reward)

            # Never discard a reward whose scoreboard update failed.
            self.lotto_config.set_pending_rewards(player.name, remaining)
            if remaining:
                player.send_error_message(
                    "§cYour lotto reward could not be added to the Money scoreboard yet. "
                    "It has been saved and will be retried when you rejoin."
                )

    def show_player_menu(self, player: Player) -> None:
        """Show the player lotto menu.
        
        Args:
            player: The player to show the menu to
        """
        form = ActionForm(
            title="§6§lLottery System",
            content="§7Select a lottery to purchase entries:"
        )

        lotto_types = self.lotto_config.get_lotto_types()

        if not lotto_types:
            form.content = "§cNo lottery types are currently available."
            player.send_form(form)
            return

        for lotto in lotto_types:
            display_name = lotto["display_name"]
            prize = lotto["prize_amount"]
            cost = lotto["entry_cost"]
            entries_count = sum(lotto.get("entries", {}).values())
            
            form.add_button(
                f"§e{display_name}\n§7Prize: §a${prize:,} §7| Entry: §6${cost:,}\n§7Total Entries: §b{entries_count}",
                on_click=lambda p, lid=lotto["id"]: self.show_entry_purchase_form(p, lid)
            )

        player.send_form(form)

    def show_entry_purchase_form(self, player: Player, lotto_id: str) -> None:
        """Show the entry purchase form.
        
        Args:
            player: The player to show the form to
            lotto_id: ID of the lotto type
        """
        lotto = self.lotto_config.get_lotto_type(lotto_id)
        if not lotto:
            player.send_error_message("§cLottery type not found.")
            return

        form = ModalForm(
            title=f"§6{lotto['display_name']}",
            submit_button="§aPurchase Entries"
        )

        form.add_control(
            TextInput(
                label="How many entries do you want to purchase?",
                placeholder="Enter a number (e.g., 1, 5, 10)",
                default_value="1"
            )
        )

        def on_submit(p: Player, data: str) -> None:
            import json
            try:
                values = json.loads(data)
                count_str = values[0].strip()
                
                if not count_str.isdigit():
                    p.send_error_message("§cPlease enter a valid number.")
                    return
                
                count = int(count_str)
                if count <= 0:
                    p.send_error_message("§cPlease enter a positive number.")
                    return

                total_cost = lotto["entry_cost"] * count
                
                # Check if player has enough money
                if not self.has_money(p, total_cost):
                    p.send_error_message(f"§cYou need ${total_cost:,} to purchase {count} entries. Check your Money score.")
                    return

                # Take the money
                if not self.take_money(p, total_cost):
                    p.send_error_message("§cFailed to process payment.")
                    return

                # Add entries
                self.lotto_config.add_entry(lotto_id, p.name, count)

                p.send_message(f"§aSuccessfully purchased {count} entries for ${total_cost:,}!")
                p.send_message(f"§7You now have §b{lotto.get('entries', {}).get(p.name, count)} §7total entries in {lotto['display_name']}.")
                
            except Exception as e:
                p.send_error_message(f"§cError processing purchase: {str(e)}")

        form.on_submit = on_submit
        player.send_form(form)

    def show_admin_menu(self, player: Player) -> None:
        """Show the admin configuration menu.
        
        Args:
            player: The operator to show the menu to
        """
        form = ActionForm(
            title="§c§lLotto Admin Menu",
            content="§7Configure lottery system:"
        )

        form.add_button("§aAdd New Lotto", on_click=lambda p: self.show_add_lotto_form(p))
        form.add_button("§eEdit Lotto", on_click=lambda p: self.show_edit_lotto_menu(p))
        form.add_button("§cRemove Lotto", on_click=lambda p: self.show_remove_lotto_menu(p))
        form.add_button("§bChange Interaction Block", on_click=lambda p: self.show_change_block_form(p))
        form.add_button("§6View Lotto Stats", on_click=lambda p: self.show_stats_menu(p))

        player.send_form(form)

    def show_add_lotto_form(self, player: Player) -> None:
        """Show the form to add a new lotto type.
        
        Args:
            player: The operator
        """
        form = ModalForm(
            title="§aAdd New Lotto",
            submit_button="§aCreate Lotto"
        )

        form.add_control(TextInput(label="Lotto ID (unique identifier)", placeholder="e.g., lotto_5000"))
        form.add_control(TextInput(label="Display Name", placeholder="e.g., 5,000 Prize"))
        form.add_control(TextInput(label="Prize Amount", placeholder="e.g., 5000"))
        form.add_control(TextInput(label="Entry Cost", placeholder="e.g., 500"))
        form.add_control(TextInput(label="Draw Frequency (days)", placeholder="e.g., 7"))

        def on_submit(p: Player, data: str) -> None:
            import json
            try:
                values = json.loads(data)
                lotto_id = values[0].strip()
                display_name = values[1].strip()
                prize = int(values[2].strip())
                cost = int(values[3].strip())
                frequency = int(values[4].strip())

                if not lotto_id or not display_name:
                    p.send_error_message("§cLotto ID and Display Name are required.")
                    return

                # Check if ID already exists
                if self.lotto_config.get_lotto_type(lotto_id):
                    p.send_error_message("§cA lotto with this ID already exists.")
                    return

                new_lotto = {
                    "id": lotto_id,
                    "display_name": display_name,
                    "prize_amount": prize,
                    "entry_cost": cost,
                    "draw_frequency_days": frequency,
                    "last_draw_time": int(time.time()),
                    "entries": {}
                }

                self.lotto_config.add_lotto_type(new_lotto)
                # Periodic checker will pick up the new lotto automatically
                
                p.send_message(f"§aSuccessfully created lotto: {display_name}")

            except ValueError:
                p.send_error_message("§cInvalid number format.")
            except Exception as e:
                p.send_error_message(f"§cError: {str(e)}")

        form.on_submit = on_submit
        player.send_form(form)

    def show_edit_lotto_menu(self, player: Player) -> None:
        """Show menu to select a lotto to edit.
        
        Args:
            player: The operator
        """
        lotto_types = self.lotto_config.get_lotto_types()

        if not lotto_types:
            player.send_error_message("§cNo lottery types available to edit.")
            return

        form = ActionForm(
            title="§eEdit Lotto",
            content="§7Select a lottery to edit:"
        )

        for lotto in lotto_types:
            form.add_button(
                f"§e{lotto['display_name']}\n§7ID: {lotto['id']}",
                on_click=lambda p, lid=lotto["id"]: self.show_edit_lotto_form(p, lid)
            )

        player.send_form(form)

    def show_edit_lotto_form(self, player: Player, lotto_id: str) -> None:
        """Show the form to edit a lotto type.
        
        Args:
            player: The operator
            lotto_id: ID of the lotto to edit
        """
        lotto = self.lotto_config.get_lotto_type(lotto_id)
        if not lotto:
            player.send_error_message("§cLotto not found.")
            return

        form = ModalForm(
            title=f"§eEdit {lotto['display_name']}",
            submit_button="§aSave Changes"
        )

        form.add_control(TextInput(label="Display Name", default_value=lotto["display_name"]))
        form.add_control(TextInput(label="Prize Amount", default_value=str(lotto["prize_amount"])))
        form.add_control(TextInput(label="Entry Cost", default_value=str(lotto["entry_cost"])))
        form.add_control(TextInput(label="Draw Frequency (days)", default_value=str(lotto["draw_frequency_days"])))

        def on_submit(p: Player, data: str) -> None:
            import json
            try:
                values = json.loads(data)
                updates = {
                    "display_name": values[0].strip(),
                    "prize_amount": int(values[1].strip()),
                    "entry_cost": int(values[2].strip()),
                    "draw_frequency_days": int(values[3].strip())
                }

                self.lotto_config.update_lotto_type(lotto_id, updates)
                # Periodic checker will pick up the new frequency automatically
                
                p.send_message(f"§aSuccessfully updated {updates['display_name']}")

            except ValueError:
                p.send_error_message("§cInvalid number format.")
            except Exception as e:
                p.send_error_message(f"§cError: {str(e)}")

        form.on_submit = on_submit
        player.send_form(form)

    def show_remove_lotto_menu(self, player: Player) -> None:
        """Show menu to select a lotto to remove.
        
        Args:
            player: The operator
        """
        lotto_types = self.lotto_config.get_lotto_types()

        if not lotto_types:
            player.send_error_message("§cNo lottery types available to remove.")
            return

        form = ActionForm(
            title="§cRemove Lotto",
            content="§7Select a lottery to remove:"
        )

        for lotto in lotto_types:
            form.add_button(
                f"§c{lotto['display_name']}\n§7ID: {lotto['id']}",
                on_click=lambda p, lid=lotto["id"], name=lotto["display_name"]: self.confirm_remove_lotto(p, lid, name)
            )

        player.send_form(form)

    def confirm_remove_lotto(self, player: Player, lotto_id: str, display_name: str) -> None:
        """Show confirmation dialog for removing a lotto.
        
        Args:
            player: The operator
            lotto_id: ID of the lotto to remove
            display_name: Display name for confirmation
        """
        form = MessageForm(
            title="§cConfirm Removal",
            content=f"§7Are you sure you want to remove §e{display_name}§7?\n\n§cThis action cannot be undone!",
            button1="§cRemove",
            button2="§7Cancel"
        )

        def on_submit(p: Player, button: int) -> None:
            if button == 0:  # Remove button
                # Remove from config - periodic checker will no longer find this lotto
                self.lotto_config.remove_lotto_type(lotto_id)
                p.send_message(f"§aSuccessfully removed {display_name}")
            else:
                p.send_message("§7Removal cancelled.")

        form.on_submit = on_submit
        player.send_form(form)

    def show_change_block_form(self, player: Player) -> None:
        """Show form to change the interaction block.
        
        Args:
            player: The operator
        """
        form = ModalForm(
            title="§bChange Interaction Block",
            submit_button="§aSave"
        )

        current_block = self.lotto_config.get_interaction_block()
        form.add_control(
            TextInput(
                label="Block Identifier",
                placeholder="e.g., ninjos:lotto",
                default_value=current_block
            )
        )

        def on_submit(p: Player, data: str) -> None:
            import json
            try:
                values = json.loads(data)
                new_block = values[0].strip()
                
                if not new_block:
                    p.send_error_message("§cBlock identifier cannot be empty.")
                    return

                self.lotto_config.set_interaction_block(new_block)
                p.send_message(f"§aInteraction block changed to: {new_block}")

            except Exception as e:
                p.send_error_message(f"§cError: {str(e)}")

        form.on_submit = on_submit
        player.send_form(form)

    def show_stats_menu(self, player: Player) -> None:
        """Show statistics for all lotto types.
        
        Args:
            player: The operator
        """
        lotto_types = self.lotto_config.get_lotto_types()

        if not lotto_types:
            player.send_error_message("§cNo lottery types available.")
            return

        form = ActionForm(
            title="§6Lotto Statistics",
            content="§7View detailed statistics:"
        )

        for lotto in lotto_types:
            entries = lotto.get("entries", {})
            total_entries = sum(entries.values())
            unique_players = len(entries)
            
            form.add_button(
                f"§e{lotto['display_name']}\n§7Entries: §b{total_entries} §7| Players: §a{unique_players}",
                on_click=lambda p, lid=lotto["id"]: self.show_detailed_stats(p, lid)
            )

        player.send_form(form)

    def show_detailed_stats(self, player: Player, lotto_id: str) -> None:
        """Show detailed statistics for a specific lotto.
        
        Args:
            player: The operator
            lotto_id: ID of the lotto
        """
        lotto = self.lotto_config.get_lotto_type(lotto_id)
        if not lotto:
            player.send_error_message("§cLotto not found.")
            return

        entries = lotto.get("entries", {})
        total_entries = sum(entries.values())
        
        content = f"§e{lotto['display_name']}\n\n"
        content += f"§7Prize: §a${lotto['prize_amount']:,}\n"
        content += f"§7Entry Cost: §6${lotto['entry_cost']:,}\n"
        content += f"§7Draw Frequency: §b{lotto['draw_frequency_days']} days\n"
        content += f"§7Total Entries: §b{total_entries}\n"
        content += f"§7Unique Players: §a{len(entries)}\n\n"
        
        if entries:
            content += "§7§lTop Participants:\n"
            sorted_entries = sorted(entries.items(), key=lambda x: x[1], reverse=True)[:10]
            for i, (name, count) in enumerate(sorted_entries, 1):
                content += f"§7{i}. §f{name}§7: §b{count} entries\n"

        form = ActionForm(
            title="§6Detailed Statistics",
            content=content
        )
        
        form.add_button("§aBack to Stats", on_click=lambda p: self.show_stats_menu(p))
        player.send_form(form)

    def schedule_draw_checker(self) -> None:
        """Start the periodic draw checker task."""
        def check_callback():
            self.check_draws()
        
        # Schedule repeating task every 5 minutes
        task = self.server.scheduler.run_task(
            self, 
            check_callback, 
            delay=self.CHECK_INTERVAL_TICKS,  # Initial delay
            period=self.CHECK_INTERVAL_TICKS  # Repeat interval
        )
        self.check_task_id = task.task_id
        self.logger.info("Draw checker started - checking every 5 minutes")
        
        # Also do an immediate check in case any draws are overdue
        self.check_draws()

    def check_draws(self) -> None:
        """Check all lotto types and perform draws if they are due."""
        current_time = int(time.time())
        
        for lotto in self.lotto_config.get_lotto_types():
            lotto_id = lotto["id"]
            frequency_seconds = lotto["draw_frequency_days"] * 24 * 60 * 60
            last_draw = lotto.get("last_draw_time", 0)
            next_draw_time = last_draw + frequency_seconds
            
            # Calculate time until next draw for logging
            time_remaining = next_draw_time - current_time
            
            if time_remaining <= 0:
                # Draw is due!
                self.logger.info(f"Draw due for {lotto['display_name']} - performing draw now")
                self.perform_draw(lotto_id)
            else:
                # Log next draw time (only on startup or when verbose)
                days = time_remaining // 86400
                hours = (time_remaining % 86400) // 3600
                minutes = (time_remaining % 3600) // 60
                self.logger.info(f"Next draw for {lotto['display_name']}: {days}d {hours}h {minutes}m")

    def perform_draw(self, lotto_id: str) -> None:
        """Perform a lottery drawing.
        
        Args:
            lotto_id: ID of the lotto type
        """
        lotto = self.lotto_config.get_lotto_type(lotto_id)
        if not lotto:
            return

        entries = lotto.get("entries", {})
        if not entries:
            self.server.broadcast_message(f"§6§l[LOTTO] §7No entries for {lotto['display_name']}, no winner this time.")
            self.lotto_config.update_last_draw_time(lotto_id, int(time.time()))
            # Periodic checker will handle next draw timing
            return

        # Create weighted pool
        pool = []
        for player_name, count in entries.items():
            pool.extend([player_name] * count)

        # Select random winner
        winner_name = random.choice(pool)
        prize_amount = lotto["prize_amount"]

        # Try to find online player
        winner = self.server.get_player(winner_name)
        
        reward_delivered = winner is not None and self.give_money(winner, prize_amount)

        if reward_delivered:
            winner.send_message("§a§l§n🎉 CONGRATULATIONS! 🎉")
            winner.send_message(f"§a§lYou won ${prize_amount:,} from the {lotto['display_name']} lotto!")
        else:
            # Offline players and failed online payouts are both persisted for retry.
            self.lotto_config.add_pending_reward(winner_name, lotto_id, prize_amount)
            if winner is not None:
                winner.send_error_message(
                    "§cYour lotto prize could not be added to the Money scoreboard. "
                    "It has been saved and will be retried when you rejoin."
                )
                self.logger.error(
                    f"Could not pay ${prize_amount} to online winner {winner_name}; reward saved for retry"
                )

        # Broadcast to server
        self.server.broadcast_message(f"§6§l[LOTTO] §e{winner_name} §7won §a${prize_amount:,} §7from the §b{lotto['display_name']}§7!")

        # Clear entries and update time
        self.lotto_config.clear_entries(lotto_id)
        self.lotto_config.update_last_draw_time(lotto_id, int(time.time()))
        # Periodic checker will handle next draw timing

    def has_money(self, player: Player, amount: int) -> bool:
        """Check if a player has enough money.

        Args:
            player: The player to check
            amount: Amount of money required

        Returns:
            True if player has enough money
        """
        try:
            score = self._get_money_score(player)
            if score is None:
                return False

            if not score.is_score_set:
                self.logger.info(f"Player {player.name} has no Money score set, treating as $0")
                current_money = 0
            else:
                current_money = score.value

            self.logger.info(f"Player {player.name} has ${current_money}, needs ${amount}")
            return current_money >= amount
        except Exception as e:
            self.logger.error(f"Failed to check money for {player.name}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    def take_money(self, player: Player, amount: int) -> bool:
        """Take money from a player.

        Args:
            player: The player to take money from
            amount: Amount to take

        Returns:
            True if successful
        """
        if not isinstance(amount, int) or amount <= 0:
            self.logger.error(f"Invalid amount to take from {player.name}: {amount!r}")
            return False
        return self._change_money(player, -amount)

    def give_money(self, player: Player, amount: int) -> bool:
        """Give money to a player.

        Args:
            player: The player to give money to
            amount: Amount to give

        Returns:
            True if successful
        """
        if not isinstance(amount, int) or amount <= 0:
            self.logger.error(f"Invalid amount to give to {player.name}: {amount!r}")
            return False
        return self._change_money(player, amount)

    def _get_money_score(self, player: Player) -> Any | None:
        """Return the player's writable Money score, if it is available."""
        scoreboard = self.server.scoreboard
        if scoreboard is None:
            self.logger.error("The server scoreboard is not available")
            return None

        objective = scoreboard.get_objective(self.MONEY_OBJECTIVE)
        if objective is None:
            self.logger.error(f"{self.MONEY_OBJECTIVE} objective not found on scoreboard")
            return None
        if not objective.is_modifiable:
            self.logger.error(f"{self.MONEY_OBJECTIVE} objective is read-only")
            return None

        return objective.get_score(player)

    def _change_money(self, player: Player, change: int) -> bool:
        """Atomically change a player's Money score through the Endstone API."""
        if not isinstance(change, int) or change == 0:
            self.logger.error(f"Invalid Money change for {player.name}: {change!r}")
            return False

        try:
            score = self._get_money_score(player)
            if score is None:
                return False

            current_money = score.value if score.is_score_set else 0
            new_money = current_money + change
            if new_money < 0:
                self.logger.warning(
                    f"Refusing to reduce {player.name}'s Money score below zero "
                    f"(${current_money} + ${change})"
                )
                return False

            score.value = new_money

            # Read the value back so a failed native update is never reported as paid.
            if not score.is_score_set or score.value != new_money:
                self.logger.error(
                    f"Money score verification failed for {player.name}: "
                    f"expected ${new_money}, got ${score.value}"
                )
                return False

            self.logger.info(
                f"Updated {player.name}'s Money score: ${current_money} -> ${new_money}"
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to update Money for {player.name}: {e}")
            return False
