
import sublime
from sublime import Region
from sublime_plugin import WindowCommand, TextCommand
import os, shutil, tempfile, subprocess
from os.path import basename, dirname, isdir, exists, join, isabs, normpath, normcase
from .common import RE_FILE, DiredBaseCommand
from . import prompt
from .show import show

# Each dired view stores its path in its local settings as 'dired_path'.

COMMANDS_HELP = """\

Browse Shortcuts:
+--------------------------+-------------+
| Command                  | Shortcut    |
|--------------------------+-------------|
| Help page                | h           |
| Toggle mark              | m           |
| Toggle all marks         | t           |
| Unmark all               | u           |
| Mark by extension        | *           |
| Rename                   | R           |
| Move                     | M           |
| Delete                   | D           |
| Create directory         | cd          |
| Create file              | cf          |
| Open file/view directory | enter/o     |
| Open in Finder/Explorer  | \           |
| Open in new window       | w           |
| Go to parent directory   | backspace   |
| Go to directory          | g           |
| Go to first              | super+up    |
| Go to last               | super+down  |
| Move to previous         | k/up        |
| Move to next             | j/down      |
| Jump to                  | /           |
| Refresh view             | r           |
| Quicklook for Mac        | space       |
+--------------------------+-------------+

In Rename Mode:
+--------------------------+-------------+
| Command                  | Shortcut    |
|--------------------------|-------------|
| Apply changes            | super+enter |
| Discard changes          | escape      |
+--------------------------+-------------+
"""

def reuse_view():
    return sublime.load_settings('dired.sublime-settings').get('dired_reuse_view', False)

class DiredCommand(WindowCommand):
    """
    Prompt for a directory to display and display it.
    """
    def run(self, immediate=False):
        if immediate:
            show(self.window, self._determine_path())
        else:
            prompt.start('Directory:', self.window, self._determine_path(), self._show)

    def _show(self, path):
        show(self.window, path)

    def _determine_path(self):
        # Use the current view's directory if it has one.
        view = self.window.active_view()
        path = view and view.file_name()
        if path:
            return dirname(path)

        # Use the first project folder if there is one.
        data = self.window.project_data()
        if data and 'folders' in data:
            folders = data['folders']
            if folders:
                return folders[0]['path']

        # Use the user's home directory.
        return os.path.expanduser('~')


class DiredRefreshCommand(TextCommand, DiredBaseCommand):
    """
    Populates or repopulates a dired view.
    """
    def run(self, edit, goto=None):
        """
        goto
            Optional filename to put the cursor on.
        """

        self.view.set_status("𝌆", " 𝌆 [h: Help] ")

        path = self.path
        names = os.listdir(path)
        f = []

        # generating dirs list first
        for name in names:
            if isdir(join(path, name)):
                name = "▸ " + name + os.sep
                f.append(name)

        # generating files list
        for name in names:
            if not isdir(join(path, name)):
                name = "≡ " + name
                f.append(name)

        marked = set(self.get_marked())

        text = [ path ]
        text.append(len(path)*'—')
        text.append('⠤')
        text.extend(f)

        self.view.set_read_only(False)

        self.view.erase(edit, Region(0, self.view.size()))
        self.view.insert(edit, 0, '\n'.join(text))
        self.view.set_syntax_file('Packages/SublimeBrowse/dired.hidden-tmLanguage')
        self.view.settings().set('dired_count', len(f))

        if marked:
            # Even if we have the same filenames, they may have moved so we have to manually
            # find them again.
            regions = []
            for line in self.view.lines(self.fileregion()):
                filename = self._remove_ui(RE_FILE.match(self.view.substr(line)).group(1))
                if filename in marked:
                    line.a = line.a + 2 # do not mark UI elements
                    regions.append(line)
            self.view.add_regions('marked', regions, 'dired.marked', '', sublime.DRAW_NO_OUTLINE)
        else:
            self.view.erase_regions('marked')

        self.view.set_read_only(True)

        # Place the cursor.
        if f:
            pt = self.fileregion(with_parent_link=True).a
            if goto:
                if isdir(join(path, goto)) and not goto.endswith(os.sep):
                    goto = "▸ " + goto + os.sep
                else:
                    goto = "≡ " + goto
                try:
                    line = f.index(goto) + 3
                    pt = self.view.text_point(line, 2)
                except ValueError:
                    pass

            self.view.sel().clear()
            self.view.sel().add(Region(pt, pt))
            self.view.show_at_center(Region(pt, pt))
        else: # empty folder?
            pt = self.view.text_point(2, 0)
            self.view.sel().clear()
            self.view.sel().add(Region(pt, pt))


class DiredNextLineCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, forward=None):
        self.move(forward)


class DiredSelect(TextCommand, DiredBaseCommand):
    def run(self, edit, new_view=False):
        path = self.path
        filenames = self.get_selected()

        # If reuse view is turned on and the only item is a directory, refresh the existing view.
        if not new_view and reuse_view():
            if len(filenames) == 1 and isdir(join(path, filenames[0])):
                fqn = join(path, filenames[0])
                show(self.view.window(), fqn, view_id=self.view.id())
                return
            elif len(filenames) == 1 and filenames[0] == "⠤":
                self.view.window().run_command("dired_up")
                return

        for filename in filenames:
            fqn = join(path, filename)
            if isdir(fqn):
                show(self.view.window(), fqn, ignore_existing=new_view)
            else:
                self.view.window().open_file(fqn)


class DiredCreateCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, which=None):
        assert which in ('file', 'directory'), "which: " + which

        # Is there a better way to do this?  Why isn't there some kind of context?  I assume
        # the command instance is global and really shouldn't have instance information.
        callback = getattr(self, 'on_done_' + which, None)
        self.view.window().show_input_panel(which.capitalize() + ':', '', callback, None, None)

    def on_done_file(self, value):
        self._on_done('file', value)

    def on_done_directory(self, value):
        self._on_done('directory', value)

    def _on_done(self, which, value):
        value = value.strip()
        if not value:
            return

        fqn = join(self.path, value)
        if exists(fqn):
            sublime.error_message('{} already exists'.format(fqn))
            return

        if which == 'directory':
            os.makedirs(fqn)
        else:
            open(fqn, 'wb')

        self.view.run_command('dired_refresh', {'goto': value})


class DiredMarkExtensionCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, ext=None):
        filergn = self.fileregion()
        if filergn.empty():
            return

        if ext is None:
            # This is the first time we've been called, so ask for the extension.
            self.view.window().show_input_panel('Extension:', '', self.on_done, None, None)
        else:
            # We have already asked for the extension but had to re-run the command to get an
            # edit object.  (Sublime's command design really sucks.)
            def _markfunc(oldmark, filename):
                return filename.endswith(ext) and True or oldmark
            self._mark(mark=_markfunc, regions=self.fileregion())

    def on_done(self, ext):
        ext = ext.strip()
        if not ext:
            return
        if not ext.startswith('.'):
            ext = '.' + ext
        self.view.run_command('dired_mark_extension', { 'ext': ext })


class DiredMarkCommand(TextCommand, DiredBaseCommand):
    """
    Marks or unmarks files.

    The mark can be set to '*' to mark a file, ' ' to unmark a file,  or 't' to toggle the
    mark.

    By default only selected files are marked, but if markall is True all files are
    marked/unmarked and the selection is ignored.

    If there is no selection and mark is '*', the cursor is moved to the next line so
    successive files can be marked by repeating the mark key binding (e.g. 'm').
    """
    def run(self, edit, mark=True, markall=False):
        assert mark in (True, False, 'toggle')

        filergn = self.fileregion()
        if filergn.empty():
            return

        # If markall is set, mark/unmark all files.  Otherwise only those that are selected.
        if markall:
            regions = [ filergn ]
        else:
            regions = self.view.sel()

        def _toggle(oldmark, filename):
            return not oldmark
        if mark == 'toggle':
            # Special internal case.
            mark = _toggle

        self._mark(mark=mark, regions=regions)

        # If there is no selection, move the cursor forward so the user can keep pressing 'm'
        # to mark successive files.
        if not markall and len(self.view.sel()) == 1 and self.view.sel()[0].empty():
            self.move(True)


class DiredDeleteCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        files = self.get_marked() or self.get_selected()
        if files:
            # Yes, I know this is English.  Not sure how Sublime is translating.
            if len(files) == 1:
                msg = "Delete {}?".format(files[0])
            else:
                msg = "Delete {} items?".format(len(files))
            if sublime.ok_cancel_dialog(msg):
                for filename in files:
                    fqn = join(self.path, filename)
                    if isdir(fqn):
                        shutil.rmtree(fqn)
                    else:
                        os.remove(fqn)
                self.view.run_command('dired_refresh')


class DiredMoveCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, **kwargs):
        if kwargs and kwargs["to"]:
            self.move_to_extreme(kwargs["to"])
            return
        else:
            files = self.get_marked() or self.get_selected()
            if files:
                prompt.start('Move to:', self.view.window(), self.path, self._move)

    def _move(self, path):
        if path == self.path:
            return

        files = self.get_marked() or self.get_selected()

        if not isabs(path):
            path = join(self.path, path)
        if not isdir(path):
            sublime.error_message('Not a valid directory: {}'.format(path))
            return

        # Move all items into the target directory.  If the target directory was also selected,
        # ignore it.
        files = self.get_marked() or self.get_selected()
        path = normpath(normcase(path))
        for filename in files:
            fqn = normpath(normcase(join(self.path, filename)))
            if fqn != path:
                shutil.move(fqn, path)
        self.view.run_command('dired_refresh')


class DiredRenameCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        if self.filecount():
            # Store the original filenames so we can compare later.
            self.view.settings().set('rename', self.get_all())
            self.view.settings().set('dired_rename_mode', True)
            self.view.set_read_only(False)

            self.set_ui_in_rename_mode(edit)
            self.view.set_status("𝌆", " 𝌆 [super+enter: Apply changes] [escape: Discard changes] ")

            # Mark the original filename lines so we can make sure they are in the same
            # place.
            r = self.fileregion()
            self.view.add_regions('rename', [ r ], '', '', sublime.DRAW_NO_OUTLINE)


class DiredRenameCancelCommand(TextCommand, DiredBaseCommand):
    """
    Cancel rename mode.
    """
    def run(self, edit):
        self.view.settings().erase('rename')
        self.view.settings().set('dired_rename_mode', False)
        goto_file_name = self.get_selected()[0]
        if goto_file_name.endswith(os.sep):
            goto_file_name = goto_file_name[0:-1]
        self.view.run_command('dired_refresh', {"goto": goto_file_name})


class DiredRenameCommitCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        if not self.view.settings().has('rename'):
            # Shouldn't happen, but we want to cleanup when things go wrong.
            self.view.run_command('dired_refresh')
            return

        before = self.view.settings().get('rename')

        # We marked the set of files with a region.  Make sure the region still has the same
        # number of files.
        after = []

        for region in self.view.get_regions('rename'):
            for line in self.view.lines(region):
                after.append(self._remove_ui(self.view.substr(line).strip()))

        if len(after) != len(before):
            sublime.error_message('You cannot add or remove lines')
            return

        if len(set(after)) != len(after):
            sublime.error_message('There are duplicate filenames')
            return

        diffs = [ (b, a) for (b, a) in zip(before, after) if b != a ]
        if diffs:
            existing = set(before)
            while diffs:
                b, a = diffs.pop(0)

                if a in existing:
                    # There is already a file with this name.  Give it a temporary name (in
                    # case of cycles like "x->z and z->x") and put it back on the list.
                    tmp = tempfile.NamedTemporaryFile(delete=False, dir=self.path).name
                    os.unlink(tmp)
                    diffs.append((tmp, a))
                    a = tmp

                print('dired rename: {} --> {}'.format(b, a))
                os.rename(join(self.path, b), join(self.path, a))
                existing.remove(b)
                existing.add(a)

        self.view.erase_regions('rename')
        self.view.settings().erase('rename')
        self.view.settings().set('dired_rename_mode', False)
        goto_file_name = self.get_selected()[0]
        if goto_file_name.endswith(os.sep):
            goto_file_name = goto_file_name[0:-1]
        self.view.run_command('dired_refresh', {"goto": goto_file_name})


class DiredUpCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        parent = dirname(self.path.rstrip(os.sep))
        if parent != os.sep:
            parent += os.sep
        if parent == self.path:
            return

        view_id = (self.view.id() if reuse_view() else None)
        show(self.view.window(), parent, view_id, goto=basename(self.path.rstrip(os.sep)))


class DiredGotoCommand(TextCommand, DiredBaseCommand):
    """
    Prompt for a new directory.
    """
    def run(self, edit):
        prompt.start('Goto:', self.view.window(), self.path, self.goto)

    def goto(self, path):
        show(self.view.window(), path, view_id=self.view.id())


class DiredQuickLookCommand(TextCommand, DiredBaseCommand):
    """
    quick look current file in mac in mac
    """
    def run(self, edit):
        files = self.get_marked() or self.get_selected()
        cmd = ["qlmanage", "-p"]
        for filename in files:
            fqn = join(self.path, filename)
            cmd.append(fqn)
        subprocess.call(cmd)


class DiredOpenExternalCommand(TextCommand, DiredBaseCommand):
    """
    open dir/file in external file explorer
    """
    def run(self, edit):
        self.view.window().run_command("open_dir", {"dir": self.path})


class DiredOpenInNewWindowCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        items = []
        executable_path = sublime.executable_path()

        if sublime.platform() == 'osx':
            app_path = executable_path[:executable_path.rfind(".app/")+5]
            executable_path = app_path+"Contents/SharedSupport/bin/subl"

        items.append(executable_path)
        items.append("-n")
        files = self.get_marked() or self.get_selected()

        for filename in files:
            fqn = join(self.path, filename)
            items.append(fqn)

        subprocess.Popen(items, cwd=self.path)


class DiredHelpCommand(TextCommand):
    def run(self, edit):
        view = self.view.window().new_file()
        view.set_name("Browse: shortcuts")
        view.set_scratch(True)
        view.settings().set('color_scheme','Packages/SublimeBrowse/dired.hidden-tmTheme')
        view.settings().set('line_numbers',False)
        view.run_command('dired_show_help')
        self.view.window().focus_view(view)


class DiredShowHelpCommand(TextCommand):
    def run(self, edit):
        self.view.erase(edit, Region(0, self.view.size()))
        self.view.insert(edit, 0, COMMANDS_HELP)
        self.view.sel().clear()
        self.view.set_read_only(True)

