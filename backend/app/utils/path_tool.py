# author: szw
"""
提供项目的绝对路径和根据相对路径输入文件的绝对路径
"""

import os


def get_project_root() -> str:
    """
    获取到当前项目的绝对路径
    :return: 当前项目的绝对路径
    """
    file_path = os.path.abspath(__file__)
    dir_path = os.path.dirname(file_path)
    project_path = os.path.dirname(dir_path)
    return project_path


def get_absolute_path(relative_path : str) -> str :
    """
    输入相对路径，输出目标文件的绝对路径
    :param relative_path: 文件的相对路径
    :return: 绝对路径
    """
    return os.path.join(get_project_root(), relative_path)

