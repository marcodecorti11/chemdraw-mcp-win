"""Unavailable Windows native commands are neither advertised nor dispatched.

ChemDraw alignment/distribution are submenu commands; Windows COM exposes no submenu items
(observed on ChemDraw 26.1), so the eight actions are refused before any native call.
"""
import asyncio
import sys
import threading

import pytest

from chemdraw_macos import native_actions

ALIGN = {'align_left', 'align_right', 'align_top', 'align_bottom', 'align_horizontal_centers',
         'align_vertical_centers', 'distribute_horizontal', 'distribute_vertical'}


def test_windows_action_list_omits_submenu_commands_and_macos_keeps_all():
    assert set(native_actions.available_actions(windows=False)) == set(native_actions.ACTIONS)
    assert set(native_actions.available_actions(windows=True)) == set(native_actions.ACTIONS) - ALIGN
    assert native_actions.available_actions(windows=False)[:2] == ('clean_structure', 'clean_reaction')


def test_refusal_is_platform_specific(monkeypatch):
    monkeypatch.setattr(native_actions, 'WINDOWS', True)
    with pytest.raises(ValueError, match='Windows COM'):
        native_actions.require_available('align_left')
    native_actions.require_available('clean_structure')
    monkeypatch.setattr(native_actions, 'WINDOWS', False)
    native_actions.require_available('align_left')


class NoNative:
    lock = threading.RLock()
    managed = {1}

    def _id(self, value):
        return int(value)

    def __getattr__(self, name):
        raise AssertionError(f'native access {name} before refusal')


def test_bridge_live_and_targeted_alignment_refused_before_native_calls(monkeypatch, tmp_path):
    from chemdraw_macos import core, live, targeted
    monkeypatch.setattr(native_actions, 'WINDOWS', True)
    with pytest.raises(ValueError, match='Windows COM'):
        core.Bridge.native_action(NoNative(), 1, 'align_left', 'all')
    with pytest.raises(ValueError, match='Windows COM'):
        live.live_action(NoNative(), 1, 'distribute_vertical', 'a' * 64, 'all')
    with pytest.raises(ValueError, match='Windows COM'):
        targeted.edit_targets_document(NoNative(), 1, str(tmp_path / 'out'), {'kind': 'molecule', 'ids': []},
                                       {'kind': 'native_align', 'action': 'align_top'})
    assert not (tmp_path / 'out').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='Schema of the Windows server')
def test_windows_server_schema_does_not_offer_alignment():
    from chemdraw_macos import server
    tools = {t.name: t for t in asyncio.run(server.get_server('full').list_tools())}
    for name in ('chemdraw_native_action', 'chemdraw_live_action'):
        offered = set(tools[name].inputSchema['properties']['action'].get('enum', []))
        assert offered and not offered & ALIGN
    assert 'alignment and distribution' in server.INSTRUCTIONS


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows GDI font inventory')
def test_windows_font_inventory_lists_installed_families_for_custom_styles():
    from chemdraw_macos import styles
    families = set(styles._installed_fonts())
    assert {'Arial', 'Times New Roman'} <= families  # shipped with every Windows installation
    assert not {'', None} & families
    styles.require_style_fonts({'BondLength': 18, 'LineWidth': 1, 'BoldWidth': 2, 'LabelSize': 10,
                                'CaptionSize': 10, 'font': 'Arial'})
    with pytest.raises(ValueError, match='Missing custom font'):
        styles.require_style_fonts({'BondLength': 18, 'LineWidth': 1, 'BoldWidth': 2, 'LabelSize': 10,
                                    'CaptionSize': 10, 'font': 'No Such Family XYZ'})
