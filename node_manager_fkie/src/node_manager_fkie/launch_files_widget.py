# Software License Agreement (BSD License)
#
# Copyright (c) 2012, Fraunhofer FKIE/US, Alexander Tiderko
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Fraunhofer nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
from python_qt_binding import loadUi
from python_qt_binding.QtCore import Qt, Signal
from python_qt_binding.QtGui import QColor, QKeySequence, QPalette

try:
    from python_qt_binding.QtGui import QSortFilterProxyModel, QItemSelectionModel
except Exception:
    from python_qt_binding.QtCore import QSortFilterProxyModel, QItemSelectionModel
try:
    from python_qt_binding.QtGui import QAbstractItemView, QApplication, QDockWidget, QWidget
except Exception:
    from python_qt_binding.QtWidgets import QAbstractItemView, QApplication, QDockWidget, QWidget

import os
import rospy

from node_manager_daemon_fkie.common import get_masteruri_from_nmd
from master_discovery_fkie.common import get_hostname
import node_manager_fkie as nm
from .common import utf8, grpc_join
from .detailed_msg_box import MessageBox
from .html_delegate import HTMLDelegate
from .launch_list_model import LaunchListModel, PathItem
from .progress_queue import ProgressQueue


class LaunchFilesWidget(QDockWidget):
    '''
    Launch file browser.
    '''

    load_signal = Signal(str, dict, str)
    ''' load the launch file with given arguments (launchfile, args, masteruri)'''
    load_profile_signal = Signal(str)
    ''' load the profile file '''
    load_as_default_signal = Signal(str, str)
    ''' load the launch file as default (path, host) '''
    edit_signal = Signal(str)
    ''' list of paths to open in an editor '''
    transfer_signal = Signal(list)
    ''' list of tuples of (url, path) selected for transfer '''

    def __init__(self, parent=None):
        '''
        Creates the window, connects the signals and init the class.
        '''
        QDockWidget.__init__(self, parent)
        # initialize parameter
        self.__current_path = os.path.expanduser('~')
        # load the UI file
        ui_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'LaunchFilesDockWidget.ui')
        loadUi(ui_file, self)
        self._current_search = ''
        pal = self.palette()
        self._default_color = pal.color(QPalette.Window)
        # initialize the progress queue
        self.progress_queue = ProgressQueue(self.ui_frame_progress_cfg, self.ui_bar_progress_cfg, self.ui_button_progress_cancel_cfg, 'Launch File')
        # initialize the view for the launch files
        self.launchlist_model = LaunchListModel(progress_queue=self.progress_queue, viewobj=self.ui_file_view)
        self.launchlist_proxy_model = QSortFilterProxyModel(self)
        self.launchlist_proxy_model.setSourceModel(self.launchlist_model)
        self.name_delegate = HTMLDelegate(check_for_ros_names=False)
        self.ui_file_view.setItemDelegateForColumn(0, self.name_delegate)
        self.ui_file_view.setModel(self.launchlist_proxy_model)
        self.ui_file_view.setAlternatingRowColors(True)
        self.ui_file_view.activated.connect(self.on_launch_selection_activated)
        self.ui_file_view.setDragDropMode(QAbstractItemView.DragOnly)
        self.ui_file_view.setDragEnabled(True)
        sm = self.ui_file_view.selectionModel()
        sm.selectionChanged.connect(self.on_ui_file_view_selection_changed)
        self.launchlist_model.pathlist_handled.connect(self.on_pathlist_handled)
        self.launchlist_model.error_on_path.connect(self.on_error_on_path)
        self.ui_search_line.refresh_signal.connect(self.set_package_filter)
        self.ui_search_line.stop_signal.connect(self.stop)
        # connect to the button signals
        self.ui_button_edit.clicked.connect(self.on_edit_xml_clicked)
        self.ui_button_new.clicked.connect(self.on_new_xml_clicked)
        self.ui_button_transfer.clicked.connect(self.on_transfer_file_clicked)
        self.ui_button_load.clicked.connect(self.on_load_xml_clicked)
        self._masteruri2name = {}

    def stop(self):
        '''
        Cancel the executing queued actions. This method must be
        called at the exit!
        '''
        self.progress_queue.stop()
        self.ui_search_line.set_process_active(False)

    def set_current_master(self, masteruri, mastername):
        self.launchlist_model.set_current_master(masteruri, mastername)
        self._masteruri2name[masteruri.rstrip(os.path.sep)] = mastername
        try:
            color = QColor.fromRgb(nm.settings().host_color(self._masteruri2name[get_masteruri_from_nmd(self.launchlist_model.current_path)], self._default_color.rgb()))
            self._new_color(color)
        except Exception as _:
            pass
#             import traceback
#             print traceback.format_exc()
#             rospy.logwarn("Error while set color in launch dock: %s" % utf8(err))

    def on_launch_selection_activated(self, activated):
        '''
        Tries to load the launch file, if one was activated.
        '''
        selected = self._pathItemsFromIndexes(self.ui_file_view.selectionModel().selectedIndexes(), False)
        for item in selected:
            try:
                self.ui_search_line.set_process_active(True)
                lfile = self.launchlist_model.expand_item(item.path, item.id)
                # self.ui_search_line.setText('')
                if lfile is not None:
                    self.ui_search_line.set_process_active(False)
                    if item.is_launch_file():
                        nm.settings().launch_history_add(item.path)
                        key_mod = QApplication.keyboardModifiers()
                        if key_mod & Qt.ShiftModifier:
                            self.load_as_default_signal.emit(item.path, None)
                        elif key_mod & Qt.ControlModifier:
                            self.launchlist_model.setPath(os.path.dirname(item.path))
                        else:
                            self.load_signal.emit(item.path, {}, None)
                    elif item.is_profile_file():
                        nm.settings().launch_history_add(item.path)
                        key_mod = QApplication.keyboardModifiers()
                        if key_mod & Qt.ControlModifier:
                            self.launchlist_model.setPath(os.path.dirname(item.path))
                        else:
                            self.load_profile_signal.emit(item.path)
                    elif item.is_config_file():
                        self.edit_signal.emit(lfile)
                if self.launchlist_model.current_path:
                    self.setWindowTitle('Launch @%s' % get_hostname(self.launchlist_model.current_grpc))
                else:
                    self.setWindowTitle('Launch files')
            except Exception as e:
                import traceback
                print traceback.format_exc()
                rospy.logwarn("Error while load launch file %s: %s" % (item, utf8(e)))
                MessageBox.warning(self, "Load error",
                                   'Error while load launch file:\n%s' % item.name,
                                   "%s" % utf8(e))
        try:
            color = QColor.fromRgb(nm.settings().host_color(self._masteruri2name[get_masteruri_from_nmd(self.launchlist_model.current_path)], self._default_color.rgb()))
            self._new_color(color)
        except Exception as _:
            pass
#             import traceback
#             print traceback.format_exc()
#             rospy.logwarn("Error while set color in launch dock: %s" % utf8(err))

    def _new_color(self, color):
        bg_style_launch_dock = "QWidget#ui_dock_widget_contents { background-color: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 %s, stop: 0.7 %s);}" % (color.name(), self._default_color.name())
        self.setStyleSheet("%s" % (bg_style_launch_dock))

    def on_pathlist_handled(self, gpath):
        self.ui_search_line.set_process_active(False)
        self.ui_button_new.setEnabled(not self.launchlist_model.is_in_root)

    def on_error_on_path(self, gpath):
        print "ERROR on_error_on_path", gpath, "c:", self.launchlist_model.current_path
        if gpath == self._current_search or gpath == self.launchlist_model.current_path:
            self.ui_search_line.set_process_active(False)

    def on_launch_selection_changed(self, selected, deselected):
        print "selection launch changed"

    def load_file(self, path, args={}, masteruri=None):
        '''
        Tries to load the launch file, if one was activated.
        '''
        if path is not None:
            if os.path.isfile(path):
                if path.endswith('.launch'):
                    self.load_signal.emit(path, args, masteruri)
                elif path.endswith('.nmprofile'):
                    self.load_profile_signal.emit(path)

    def on_ui_file_view_selection_changed(self, selected, deselected):
        '''
        On selection of a launch file, the buttons are enabled otherwise disabled.
        '''
        selected = self._pathItemsFromIndexes(self.ui_file_view.selectionModel().selectedIndexes(), False)
        for item in selected:
            islaunch = item.is_launch_file()
            isconfig = item.is_config_file()
            isprofile = item.is_profile_file()
            self.ui_button_edit.setEnabled(islaunch or isconfig or isprofile)
            self.ui_button_load.setEnabled(islaunch or isprofile)
            self.ui_button_transfer.setEnabled(islaunch or isconfig)

    def set_package_filter(self, text):
        #         if text.startswith('s '):
        #             if len(text) > 2:
        #                 search_text = text[2:]
        #                 self.launchlist_proxy_model.setFilterRegExp(QRegExp(search_text, Qt.CaseInsensitive, QRegExp.Wildcard))
        #                 import glob
        #                 print glob.glob1(self.launchlist_model.current_path, text)
        #             else:
        #                 self.ui_search_line.set_process_active(False)
        #         else:
        if text:
            if text.startswith(os.path.sep):
                self._current_search = grpc_join(self.launchlist_model.current_grpc, text)
                print "SET PATH", self._current_search
                self.launchlist_model.set_path(text)
            else:
                # search for a package
                self.launchlist_model.show_packages(text)
                self.ui_search_line.set_process_active(False)
        else:
            self.launchlist_model.reload_current_path(clear_cache=True)

    def on_edit_xml_clicked(self):
        '''
        Opens an XML editor to edit the launch file.
        '''
        selected = self._pathItemsFromIndexes(self.ui_file_view.selectionModel().selectedIndexes(), False)
        for item in selected:
            path = self.launchlist_model.expand_item(item.path, item.id)
            if path is not None:
                self.edit_signal.emit(path)

    def on_new_xml_clicked(self):
        '''
        Creates a new launch file.
        '''
        # get new file from open dialog, use last path if one exists
        if not self.launchlist_model.is_in_root:
            items = self.launchlist_model.add_new_launch()
            if items:
                index = self.launchlist_proxy_model.mapFromSource(self.launchlist_model.index(1, 0))
                self.ui_file_view.selectionModel().select(index, QItemSelectionModel.Select)
                self.ui_file_view.setCurrentIndex(index)
                self.ui_file_view.edit(index)

    def on_transfer_file_clicked(self):
        '''
        Emit the signal to copy the selected file to a remote host.
        '''
        selected = self._pathItemsFromIndexes(self.ui_file_view.selectionModel().selectedIndexes(), False)
        paths = list()
        for item in selected:
            path = self.launchlist_model.expand_item(item.path, item.id)
            if path is not None:
                paths.append(path)
        if paths:
            self.transfer_signal.emit(paths)

    def on_load_xml_clicked(self):
        '''
        Tries to load the selected launch file. The button is only enabled and this
        method is called, if the button was enabled by on_launch_selection_clicked()
        '''
        selected = self._pathItemsFromIndexes(self.ui_file_view.selectionModel().selectedIndexes(), False)
        for item in selected:
            path = self.launchlist_model.expand_item(item.path, item.id)
            if path is not None:
                nm.settings().launch_history_add(item.path)
                self.load_signal.emit(path, {}, None)

    def _pathItemsFromIndexes(self, indexes, recursive=True):
        result = []
        for index in indexes:
            if index.column() == 0:
                model_index = self.launchlist_proxy_model.mapToSource(index)
                item = self.launchlist_model.itemFromIndex(model_index)
                if item is not None and isinstance(item, PathItem):
                    result.append(item)
        return result

    def keyPressEvent(self, event):
        '''
        Defines some of shortcuts for navigation/management in launch
        list view or topics view.
        '''
        key_mod = QApplication.keyboardModifiers()
        if not self.ui_file_view.state() == QAbstractItemView.EditingState:
            # remove history file from list by pressing DEL
            if event == QKeySequence.Delete:
                selected = self._pathItemsFromIndexes(self.ui_file_view.selectionModel().selectedIndexes(), False)
                for item in selected:
                    nm.settings().launch_history_remove(item.path)
                    self.launchlist_model.reload_current_path()
            elif not key_mod and event.key() == Qt.Key_F4 and self.ui_button_edit.isEnabled():
                # open selected launch file in xml editor by F4
                self.on_edit_xml_clicked()
            elif event == QKeySequence.Find:
                # set focus to filter box for packages
                self.ui_search_line.setFocus(Qt.ActiveWindowFocusReason)
            elif event == QKeySequence.Paste:
                # paste files from clipboard
                self.launchlist_model.paste_from_clipboard()
            elif event == QKeySequence.Copy:
                # copy the selected items as file paths into clipboard
                selected = self.ui_file_view.selectionModel().selectedIndexes()
                indexes = []
                for s in selected:
                    indexes.append(self.launchlist_proxy_model.mapToSource(s))
                self.launchlist_model.copy_to_clipboard(indexes)
        if self.ui_search_line.hasFocus() and event.key() == Qt.Key_Escape:
            # cancel package filtering on pressing ESC
            self.launchlist_model.reload_current_path()
            self.ui_search_line.setText('')
            self.ui_file_view.setFocus(Qt.ActiveWindowFocusReason)
        QDockWidget.keyReleaseEvent(self, event)
