import os
import sys

from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport
from src.backend.DeckManagement.InputIdentifier import Input

from .CycleInput import CycleInput

sys.path.append(os.path.dirname(__file__))


class DDCInputPlugin(PluginBase):
    def __init__(self):
        super().__init__()

        self.cycle_input_holder = ActionHolder(
            plugin_base=self,
            action_base=CycleInput,
            action_id_suffix="CycleInput",
            action_name="Cycle Monitor Input",
            action_support={
                Input.Key: ActionInputSupport.SUPPORTED,
                Input.Dial: ActionInputSupport.UNTESTED,
                Input.Touchscreen: ActionInputSupport.UNTESTED,
            },
        )
        self.add_action_holder(self.cycle_input_holder)

        self.register(
            plugin_name="DDC Monitor Input",
            github_repo="https://github.com/amsterisk/sc-ddc-input",
            plugin_version="0.1.0",
            app_version="1.5.0-beta",
        )
