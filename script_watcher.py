"""
script_watcher.py: Reload watched script upon changes.

Copyright (C) 2015 Isaac Weaver
Author: Isaac Weaver <wisaac407@gmail.com>

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along
    with this program; if not, write to the Free Software Foundation, Inc.,
    51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
"""

bl_info = {
    "name": "Script Watcher",
    "author": "Roman Wedemeier",
    "version": (0, 8, 0),
    "blender": (4, 4, 1),
    "location": "Properties > Scene > Script Watcher",
    "description": "Reloads an external script on edits.",
    "warning": "Still in beta stage.",
    "category": "Development",
}

import importlib.util
import io
import os
import subprocess
import sys
import traceback

import bpy


# Blender 4.4.1 compatible operator base class
class SW_OT_BaseOperator:
    @classmethod
    def poll(cls, context):
        return context.scene is not None

# Script Watcher Loader
class ScriptWatcherLoader:
    def __init__(self, filepath, run_main=False):
        self.filepath = os.path.abspath(filepath)
        self.run_main = run_main
        self.module = None
        self._last_mtime = 0
        self._mod_name = self._get_mod_name()

    def _get_mod_name(self):
        dirname, filename = os.path.split(self.filepath)
        if filename == '__init__.py':
            return os.path.basename(dirname)
        return os.path.splitext(filename)[0]

    def load_module(self):
        try:
            # Remove old module if exists
            if self._mod_name in sys.modules:
                del sys.modules[self._mod_name]

            # Create new module
            spec = importlib.util.spec_from_file_location(
                self._mod_name if self.run_main else '__main__',
                self.filepath
            )
            if spec is None:
                raise ImportError(f"Could not create spec for {self.filepath}")

            self.module = importlib.util.module_from_spec(spec)
            sys.modules[self._mod_name] = self.module

            # Read and execute
            with open(self.filepath, 'r', encoding='utf-8') as f:
                code = compile(f.read(), self.filepath, 'exec')
                exec(code, self.module.__dict__)

            # Call main if requested
            if self.run_main and hasattr(self.module, 'main'):
                self.module.main()

            self._last_mtime = os.path.getmtime(self.filepath)
            return True

        except Exception as e:
            print(f"Error loading {self.filepath}:")
            traceback.print_exc()
            return False

    def check_reload(self):
        try:
            current_mtime = os.path.getmtime(self.filepath)
            if current_mtime > self._last_mtime:
                return self.load_module()
        except OSError:
            pass
        return False

# Output capture
class OutputCapture:
    def __init__(self):
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        self._output = io.StringIO()
        self._error = io.StringIO()

    def __enter__(self):
        sys.stdout = self._output
        sys.stderr = self._error
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._stdout
        sys.stderr = self._stderr

    def get_output(self):
        return self._output.getvalue(), self._error.getvalue()

# Operators
class SW_OT_WatchStart(bpy.types.Operator, SW_OT_BaseOperator):
    bl_idname = "wm.sw_watch_start"
    bl_label = "Watch Script"

    _timer = None
    loader = None

    def modal(self, context, event):
        if not context.scene.sw_settings.running:
            self.cancel(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            if context.scene.sw_settings.reload:
                context.scene.sw_settings.reload = False
                with OutputCapture() as cap:
                    self.loader.load_module()
            else:
                with OutputCapture() as cap:
                    self.loader.check_reload()

        return {'PASS_THROUGH'}

    def execute(self, context):
        settings = context.scene.sw_settings
        if settings.running:
            return {'CANCELLED'}

        filepath = bpy.path.abspath(settings.filepath)
        if not os.path.isfile(filepath):
            self.report({'ERROR'}, "Script file not found")
            return {'CANCELLED'}

        self.loader = ScriptWatcherLoader(filepath, settings.run_main)

        with OutputCapture() as cap:
            if not self.loader.load_module():
                return {'CANCELLED'}

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)

        settings.running = True
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
        if hasattr(context.scene, 'sw_settings'):
            context.scene.sw_settings.running = False

class SW_OT_WatchEnd(bpy.types.Operator, SW_OT_BaseOperator):
    bl_idname = "wm.sw_watch_end"
    bl_label = "Stop Watching"

    def execute(self, context):
        context.scene.sw_settings.running = False
        return {'FINISHED'}

class SW_OT_Reload(bpy.types.Operator, SW_OT_BaseOperator):
    bl_idname = "wm.sw_reload"
    bl_label = "Reload Script"

    def execute(self, context):
        context.scene.sw_settings.reload = True
        return {'FINISHED'}

class SW_OT_EditExternally(bpy.types.Operator, SW_OT_BaseOperator):
    bl_idname = "wm.sw_edit_externally"
    bl_label = "Edit Externally"

    def execute(self, context):
        filepath = bpy.path.abspath(context.scene.sw_settings.filepath)
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", filepath])
            elif sys.platform == "win32":
                os.startfile(filepath)
            else:
                subprocess.run(["xdg-open", filepath])
        except Exception as e:
            self.report({'ERROR'}, f"Could not open editor: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}

# UI Panel
class SW_PT_Panel(bpy.types.Panel):
    bl_label = "Script Watcher"
    bl_idname = "SCENE_PT_script_watcher"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "scene"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sw_settings

        col = layout.column()
        col.prop(settings, 'filepath')
        col.prop(settings, 'use_py_console')
        col.prop(settings, 'auto_watch_on_startup')
        col.prop(settings, 'run_main')

        if not settings.running:
            col.operator(SW_OT_WatchStart.bl_idname, icon='PLAY')
        else:
            row = layout.row(align=True)
            row.operator(SW_OT_WatchEnd.bl_idname, icon='CANCEL')
            row.operator(SW_OT_Reload.bl_idname, icon='FILE_REFRESH')

        layout.separator()
        layout.operator(SW_OT_EditExternally.bl_idname, icon='TEXT')

# Property Group
class SW_Settings(bpy.types.PropertyGroup):
    running: bpy.props.BoolProperty(
        name="Running",
        default=False
    )

    reload: bpy.props.BoolProperty(
        name="Reload",
        default=False
    )

    filepath: bpy.props.StringProperty(
        name="Script",
        description="Script file to watch",
        subtype='FILE_PATH'
    )

    use_py_console: bpy.props.BoolProperty(
        name="Use Python Console",
        description="Show output in Blender's Python console",
        default=True
    )

    auto_watch_on_startup: bpy.props.BoolProperty(
        name="Auto Start",
        description="Automatically start watching when loading this blend file",
        default=False
    )

    run_main: bpy.props.BoolProperty(
        name="Run main()",
        description="Call main() function instead of executing as __main__",
        default=False
    )

# Registration
classes = (
    SW_Settings,
    SW_OT_WatchStart,
    SW_OT_WatchEnd,
    SW_OT_Reload,
    SW_OT_EditExternally,
    SW_PT_Panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.sw_settings = bpy.props.PointerProperty(type=SW_Settings)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.sw_settings

if __name__ == "__main__":
    register()
