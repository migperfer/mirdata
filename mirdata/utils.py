# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from collections import namedtuple
import hashlib
import os
import json
import tarfile
import shutil
import zipfile
import requests
from requests.exceptions import HTTPError
from tqdm import tqdm

MIR_DATASETS_DIR = os.path.join(os.getenv("HOME", "/tmp"), "mir_datasets")
VALIDATED_FILE_NAME = '_VALIDATED'
INVALID_FILE_NAME = '_INVALID.json'


def md5(file_path):
    """Get md5 hash of a file.

    Parameters
    ----------
    file_path: str
        File path.

    Returns
    -------
    md5_hash: str
        md5 hash of data in file_path
    """
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as fhandle:
        for chunk in iter(lambda: fhandle.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def log_message(message, silence=False):
    if not silence:
        print(message)


def check_index(dataset_index, data_home, silence=False):
    missing_files = {}
    invalid_checksums = {}

    # loop over track ids
    for track_id, track in dataset_index.items():
        # loop over each data file for this track id
        for key in track.keys():
            filepath = track[key][0]
            checksum = track[key][1]
            if filepath is not None:
                local_path = get_local_path(data_home, filepath)
                # validate that the file exists on disk
                if not os.path.exists(local_path):
                    if track_id not in missing_files.keys():
                        missing_files[track_id] = []
                    missing_files[track_id].append(local_path)
                # validate that the checksum matches
                elif md5(local_path) != checksum:
                    if track_id not in invalid_checksums.keys():
                        invalid_checksums[track_id] = []
                    invalid_checksums[track_id].append(local_path)

    return missing_files, invalid_checksums


def validator(dataset_index, data_home, dataset_path, silence=False):
    if check_validated(dataset_path):
        return {}, {}

    missing_files, invalid_checksums = check_index(dataset_index, data_home, silence)

    # print path of any missing files
    for track_id in missing_files.keys():
        if len(missing_files[track_id]) > 0:
            log_message("Files missing for {}:".format(track_id), silence)
            for fpath in missing_files[track_id]:
                log_message(fpath, silence)
            log_message("-" * 20, silence)

    # print path of any invalid checksums
    for track_id in invalid_checksums.keys():
        if len(invalid_checksums[track_id]) > 0:
            log_message("Invalid checksums for {}:".format(track_id), silence)
            for fpath in invalid_checksums[track_id]:
                log_message(fpath, silence)
            log_message("-" * 20, silence)

    if missing_files or invalid_checksums:
        create_invalid(dataset_path, missing_files, invalid_checksums)
    else:
        create_validated(dataset_path)

    return missing_files, invalid_checksums


F0Data = namedtuple(
    'F0Data',
    ['times', 'frequencies', 'confidence']
)

LyricsData = namedtuple(
    'LyricsData',
    ['start_times', 'end_times', 'lyrics', 'pronounciations']
)

SectionData = namedtuple(
    'SectionsData',
    ['start_times', 'end_times', 'sections']
)

BeatData = namedtuple(
    'BeatsData',
    ['beats_times', 'beats_positions']
)

ChordData = namedtuple(
    'ChordsData',
    ['start_times', 'end_times', 'chords']
)

KeyData = namedtuple(
    'KeyData',
    ['start_times', 'end_times', 'keys']
)


def get_local_path(data_home, rel_path):
    if rel_path is None:
        return None
    if data_home is None:
        return os.path.join(MIR_DATASETS_DIR, rel_path)
    else:
        return os.path.join(data_home, rel_path)


def get_save_path(data_home):
    """Get path to save a file given value of `data_home`, and create it if it
    does not exist.

    Parameters
    ----------
    data_home: str or None
        If string, `save_path` is set to data_home.
        If None, `save_path` is set to the default MIR_DATASETS_DIR value.

    Returns
    ------_
    save_path: str
        Path to save data.
    """
    if data_home is None:
        save_path = MIR_DATASETS_DIR
    else:
        save_path = data_home

    if not os.path.exists(save_path):
        os.makedirs(save_path)

    return save_path


RemoteFileMetadata = namedtuple('RemoteFileMetadata',
                                ['filename', 'url', 'checksum'])


class DownloadProgressBar(tqdm):
    def update_to(self, b=1, bsize=1, tsize=None):
        if tsize is not None:
            self.total = tsize
        self.update(b * bsize - self.n)


def download_large_file(url, download_path, callback=lambda: None):
    response = requests.get(url)
    response.raise_for_status()
    with open(download_path, 'wb') as handle:
        for block in response.iter_content(4096):
            handle.write(block)
            callback()
    return download_path


def download_from_remote(remote, data_home=None, force_overwrite=False):
    """Download a remote dataset into path
    Fetch a dataset pointed by remote's url, save into path using remote's
    filename and ensure its integrity based on the MD5 Checksum of the
    downloaded file.

    Adapted from scikit-learn's sklearn.datasets.base._fetch_remote.

    Parameters
    -----------
    remote: RemoteFileMetadata
        Named tuple containing remote dataset meta information: url, filename
        and checksum
    data_home: string
        Directory to save the file to.
    force_overwrite: bool
        If True, overwrite existing file with the downloaded file.
        If False, does not overwrite, but checks that checksum is consistent.

    Returns
    -------
    file_path: string
        Full path of the created file.
    """
    download_path = (
        os.path.join(MIR_DATASETS_DIR, remote.filename) if data_home is None
        else os.path.join(data_home, remote.filename)
    )
    if not os.path.exists(download_path) or force_overwrite:
        # If file doesn't exist or we want to overwrite, download it
        with DownloadProgressBar(unit='B', unit_scale=True,
                                 miniters=1,
                                 desc=remote.url.split('/')[-1]) as t:
            try:
                download_large_file(remote.url, download_path, t.update_to)
            except HTTPError:
                error_msg = """
                            mirdata failed to download the dataset!
                            Please try again in a few minutes.
                            If this error persists, please raise an issue at
                            https://github.com/mir-dataset-loaders/mirdata,
                            and tag it with "broken-link".
                            """
                raise HTTPError(error_msg)

    checksum = md5(download_path)
    if remote.checksum != checksum:
        raise IOError("{} has an MD5 checksum ({}) "
                      "differing from expected ({}), "
                      "file may be corrupted.".format(download_path, checksum,
                                                      remote.checksum))
    return download_path


def unzip(zip_path, save_dir, cleanup=False):
    """Unzip a zip file to a specified save location.

    Parameters
    ----------
    zip_path: str
        Path to zip file
    save_dir: str
        Path to save unzipped data
    cleanup: bool, default=False
        If True, remove zipfile after unzipping.
    """
    zfile = zipfile.ZipFile(zip_path, 'r')
    zfile.extractall(save_dir)
    zfile.close()
    if cleanup:
        os.remove(zip_path)


def untar(tar_path, save_dir, cleanup=False):
    """Untar a tar file to a specified save location.

    Parameters
    ----------
    tar_path: str
        Path to tar file
    save_dir: str
        Path to save untarred data
    cleanup: bool, default=False
        If True, remove tarfile after untarring.
    """
    if tar_path.endswith("tar.gz"):
        tfile = tarfile.open(tar_path, "r:gz")
    else:
        tfile = tarfile.TarFile(tar_path, 'r')
    tfile.extractall(save_dir)
    tfile.close()
    if cleanup:
        os.remove(tar_path)


def load_json_index(filename):
    CWD = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(CWD, 'indexes', filename)) as f:
        return json.load(f)


def check_validated(dataset_path):
    return os.path.exists(os.path.join(dataset_path, VALIDATED_FILE_NAME))


def create_validated(dataset_path):
    open(os.path.join(dataset_path, VALIDATED_FILE_NAME), 'a').close()


def create_invalid(dataset_path, missing_files, invalid_checksums):
    with open(os.path.join(dataset_path, INVALID_FILE_NAME), 'w') as f:
        json.dump({'missing_files': missing_files,
                   'invalid_checksums': invalid_checksums}, f, indent=2)


def force_overwrite_all(remote, dataset_path, data_home=None):
    if remote:
        download_path = (
            os.path.join(MIR_DATASETS_DIR, remote.filename) if data_home is None
            else os.path.join(data_home, remote.filename)
        )
        os.remove(download_path)

    if dataset_path:
        shutil.rmtree(dataset_path)
