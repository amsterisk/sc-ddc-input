import os
import sys

from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport
from src.backend.DeckManagement.InputIdentifier import Input

from .CycleState import CycleState
from .settings_ui import PluginSettings

sys.path.append(os.path.dirname(__file__))

_SUPPORT = {
    Input.Key: ActionInputSupport.SUPPORTED,
    Input.Dial: ActionInputSupport.UNTESTED,
    Input.Touchscreen: ActionInputSupport.UNTESTED,
}


class DDCInputPlugin(PluginBase):
    def __init__(self):
        super().__init__()

        self.cycle_state_holder = ActionHolder(
            plugin_base=self,
            action_base=CycleState,
            action_id_suffix="CycleState",
            action_name="Cycle Monitor State",
            action_support=dict(_SUPPORT),
        )
        self.add_action_holder(self.cycle_state_holder)

        self.register(
            plugin_name="DDC Monitor Input",
            github_repo="https://github.com/amsterisk/sc-ddc-input",
            plugin_version="0.2.0",
            app_version="1.5.0-beta",
        )

        self._settings_ui = PluginSettings(self)

    def get_settings_area(self):
        return self._settings_ui.get_settings_area()
