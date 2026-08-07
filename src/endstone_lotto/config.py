"""Configuration manager for the Lotto plugin."""
import json
from pathlib import Path
from typing import Dict, List, Any


class LottoConfig:
    """Manages the lotto plugin configuration."""

    def __init__(self, config_path: Path):
        """Initialize the config manager.
        
        Args:
            config_path: Path to the configuration file
        """
        self.config_path = config_path
        self.data: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load configuration from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    content = f.read()
                    # Handle empty files
                    if not content.strip():
                        self.data = self._get_default_config()
                        self.save()
                    else:
                        self.data = json.loads(content)
            except json.JSONDecodeError:
                # Handle corrupted JSON - fall back to defaults
                self.data = self._get_default_config()
                self.save()
        else:
            self.data = self._get_default_config()
            self.save()

    def save(self) -> None:
        """Save configuration to file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(self.data, f, indent=4)

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration.
        
        Returns:
            Default configuration dictionary
        """
        return {
            "interaction_block": "ninjos:lotto",
            "lotto_types": [
                {
                    "id": "lotto_10000",
                    "display_name": "10,000 Prize",
                    "prize_amount": 10000,
                    "entry_cost": 1000,
                    "draw_frequency_days": 7,
                    "last_draw_time": 0,
                    "entries": {}
                }
            ],
            "pending_rewards": {}
        }

    def get_interaction_block(self) -> str:
        """Get the block type that triggers the lotto menu.
        
        Returns:
            Block identifier string
        """
        return self.data.get("interaction_block", "ninjos:lotto")

    def set_interaction_block(self, block_id: str) -> None:
        """Set the block type that triggers the lotto menu.
        
        Args:
            block_id: New block identifier
        """
        self.data["interaction_block"] = block_id
        self.save()

    def get_lotto_types(self) -> List[Dict[str, Any]]:
        """Get all lotto types.
        
        Returns:
            List of lotto type configurations
        """
        return self.data.get("lotto_types", [])

    def get_lotto_type(self, lotto_id: str) -> Dict[str, Any] | None:
        """Get a specific lotto type by ID.
        
        Args:
            lotto_id: ID of the lotto type
            
        Returns:
            Lotto type configuration or None if not found
        """
        for lotto in self.data.get("lotto_types", []):
            if lotto["id"] == lotto_id:
                return lotto
        return None

    def add_lotto_type(self, lotto_type: Dict[str, Any]) -> None:
        """Add a new lotto type.
        
        Args:
            lotto_type: Lotto type configuration
        """
        if "lotto_types" not in self.data:
            self.data["lotto_types"] = []
        self.data["lotto_types"].append(lotto_type)
        self.save()

    def update_lotto_type(self, lotto_id: str, updates: Dict[str, Any]) -> bool:
        """Update an existing lotto type.
        
        Args:
            lotto_id: ID of the lotto type to update
            updates: Dictionary of fields to update
            
        Returns:
            True if updated, False if not found
        """
        for lotto in self.data.get("lotto_types", []):
            if lotto["id"] == lotto_id:
                lotto.update(updates)
                self.save()
                return True
        return False

    def remove_lotto_type(self, lotto_id: str) -> bool:
        """Remove a lotto type.
        
        Args:
            lotto_id: ID of the lotto type to remove
            
        Returns:
            True if removed, False if not found
        """
        lotto_types = self.data.get("lotto_types", [])
        for i, lotto in enumerate(lotto_types):
            if lotto["id"] == lotto_id:
                lotto_types.pop(i)
                self.save()
                return True
        return False

    def add_entry(self, lotto_id: str, player_name: str, count: int) -> bool:
        """Add entries for a player to a lotto.
        
        Args:
            lotto_id: ID of the lotto type
            player_name: Name of the player
            count: Number of entries to add
            
        Returns:
            True if added, False if lotto not found
        """
        lotto = self.get_lotto_type(lotto_id)
        if not lotto:
            return False
        
        if "entries" not in lotto:
            lotto["entries"] = {}
        
        current_entries = lotto["entries"].get(player_name, 0)
        lotto["entries"][player_name] = current_entries + count
        self.save()
        return True

    def clear_entries(self, lotto_id: str) -> bool:
        """Clear all entries for a lotto type.
        
        Args:
            lotto_id: ID of the lotto type
            
        Returns:
            True if cleared, False if lotto not found
        """
        lotto = self.get_lotto_type(lotto_id)
        if not lotto:
            return False
        
        lotto["entries"] = {}
        self.save()
        return True

    def update_last_draw_time(self, lotto_id: str, timestamp: int) -> bool:
        """Update the last draw time for a lotto type.
        
        Args:
            lotto_id: ID of the lotto type
            timestamp: Unix timestamp of the draw
            
        Returns:
            True if updated, False if lotto not found
        """
        return self.update_lotto_type(lotto_id, {"last_draw_time": timestamp})

    def add_pending_reward(self, player_name: str, lotto_id: str, amount: int) -> None:
        """Add a pending reward for an offline player.
        
        Args:
            player_name: Name of the player
            lotto_id: ID of the lotto type won
            amount: Amount of money to award
        """
        if "pending_rewards" not in self.data:
            self.data["pending_rewards"] = {}
        
        if player_name not in self.data["pending_rewards"]:
            self.data["pending_rewards"][player_name] = []
        
        self.data["pending_rewards"][player_name].append({
            "lotto_id": lotto_id,
            "amount": amount
        })
        self.save()

    def get_pending_rewards(self, player_name: str) -> List[Dict[str, Any]]:
        """Get pending rewards for a player.
        
        Args:
            player_name: Name of the player
            
        Returns:
            List of pending reward dictionaries
        """
        return self.data.get("pending_rewards", {}).get(player_name, [])

    def clear_pending_rewards(self, player_name: str) -> None:
        """Clear pending rewards for a player.
        
        Args:
            player_name: Name of the player
        """
        if "pending_rewards" in self.data and player_name in self.data["pending_rewards"]:
            del self.data["pending_rewards"][player_name]
            self.save()

    def set_pending_rewards(self, player_name: str, rewards: List[Dict[str, Any]]) -> None:
        """Replace a player's pending rewards.

        This is used after a delivery attempt so only rewards that were actually
        paid are removed from persistent storage.

        Args:
            player_name: Name of the player
            rewards: Rewards that still need to be delivered
        """
        if rewards:
            if "pending_rewards" not in self.data:
                self.data["pending_rewards"] = {}
            self.data["pending_rewards"][player_name] = rewards
        elif "pending_rewards" in self.data:
            self.data["pending_rewards"].pop(player_name, None)
        self.save()
