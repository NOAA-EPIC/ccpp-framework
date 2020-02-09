#!/usr/bin/env python

"""
Create CCPP parameterization caps, host-model interface code,
physics suite runtime code, and CCPP framework documentation.
"""

# Python library imports
from __future__ import absolute_import
from __future__ import unicode_literals
from __future__ import print_function

import argparse
import sys
import os
import os.path
import logging
import re
# CCPP framework imports
from parse_tools import init_log, set_log_level, context_string
from parse_tools import CCPPError, ParseInternalError
from file_utils import check_for_writeable_file
from file_utils import create_file_list
from fortran_tools import parse_fortran_file, FortranWriter
from host_model import HostModel
from host_cap import write_host_cap
from ccpp_suite import API, COPYRIGHT, KINDS_MODULE, KINDS_FILENAME
from metadata_table import MetadataTable
from ccpp_datafile import generate_ccpp_datatable

## Init this now so that all Exceptions can be trapped
_LOGGER = init_log(os.path.basename(__file__))

_EPILOG = '''
'''

## Recognized Fortran filename extensions
_FORTRAN_FILENAME_EXTENSIONS = ['F90', 'f90', 'F', 'f']

## Metadata table types which can have extra variables in Fortran
_EXTRA_VARIABLE_TABLE_TYPES = ['module', 'host', 'ddt']

## Metadata table types where order is significant
_ORDERED_TABLE_TYPES = ['scheme']

## header for kinds file
_KINDS_HEADER = '''
!>
!! @brief Auto-generated kinds for CCPP
!!
!
'''

###############################################################################
def parse_command_line(args, description):
###############################################################################
    """Create an ArgumentParser to parse and return command-line arguments"""
    format = argparse.RawTextHelpFormatter
    parser = argparse.ArgumentParser(description=description,
                                     formatter_class=format, epilog=_EPILOG)

    parser.add_argument("--host-files", metavar='<host files filename>',
                        type=str, required=True,
                        help="""Comma separated list of host filenames to process
Filenames with a '.meta' suffix are treated as host model metadata files
Filenames with a '.txt' suffix are treated as containing a list of .meta
filenames""")

    parser.add_argument("--scheme-files", metavar='<scheme files filename>',
                        type=str, required=True,
                        help="""Comma separated list of scheme filenames to process
Filenames with a '.meta' suffix are treated as scheme metadata files
Filenames with a '.txt' suffix are treated as containing a list of .meta
filenames""")

    parser.add_argument("--suites", metavar='<Suite definition file(s)>',
                        type=str, required=True,
                        help="""Comma separated list of suite definition filenames to process
Filenames with a '.xml' suffix are treated as suite definition XML files
Other filenames are treated as containing a list of .xml filenames""")

    parser.add_argument("--preproc-directives",
                        metavar='VARDEF1[,VARDEF2 ...]', type=str, default='',
                        help="Proprocessor directives used to correctly parse source files")

    parser.add_argument("--ccpp-datafile", type=str,
                        metavar='<data table XML filename>',
                        default="datatable.xml",
                        help="Filename for information on content generated by the CCPP Framework")

    parser.add_argument("--output-root", type=str,
                        metavar='<directory for generated files>',
                        default=os.getcwd(),
                        help="directory for generated files")

    parser.add_argument("--host-name", type=str, default='',
                        help='''Name of host model to use in CCPP API
If this option is passed, a host model cap is generated''')

    parser.add_argument("--clean", action='store_true',
                        help='Remove files created by this script, then exit',
                        default=False)

    parser.add_argument("--kind-phys", type=str, default='REAL64',
                        metavar="kind_phys",
                        help='Data size for real(kind_phys) data')

    parser.add_argument("--generate-docfiles",
                        metavar='HTML | Latex | HTML,Latex', type=str,
                        help="Generate LaTeX and/or HTML documentation")

    parser.add_argument("--verbose", action='count', default=0,
                        help="Log more activity, repeat for increased output")
    pargs = parser.parse_args(args)
    return pargs

###############################################################################
def delete_pathnames_from_file(capfile, logger):
###############################################################################
    """Remove all the filenames found in <capfile>, then delete <capfile>"""
    root_path = os.path.dirname(os.path.abspath(capfile))
    success = True
    with open(capfile, 'r') as infile:
        for line in infile.readlines():
            path = line.strip()
            # Skip blank lines and lines which appear to start with a comment.
            if path and (path[0] != '#') and (path[0] != '!'):
                # Check for an absolute path
                if not os.path.isabs(path):
                    # Assume relative pathnames are relative to pathsfile
                    path = os.path.normpath(os.path.join(root_path, path))
                # End if
                logger.info("Clean: Removing {}".format(path))
                try:
                    os.remove(path)
                except OSError as oserr:
                    success = False
                    errmsg = 'Unable to remove {}\n{}'
                    logger.warning(errmsg.format(path, oserr))
                # End try
            # End if (else skip blank or comment line)
        # End for
    # End with open
    logger.info("Clean: Removing {}".format(capfile))
    try:
        os.remove(capfile)
    except OSError as oserr:
        success = False
        errmsg = 'Unable to remove {}\n{}'
        logger.warning(errmsg.format(capfile, oserr))
    # End try
    if success:
        logger.info("ccpp_capgen clean successful, exiting")
    else:
        logger.info("ccpp_capgen clean encountered errors, exiting")
    # End if

###############################################################################
def find_associated_fortran_file(filename):
###############################################################################
    "Find the Fortran file associated with metadata file, <filename>"
    fort_filename = None
    lastdot = filename.rfind('.')
    ##XXgoldyXX: Should we check to make sure <filename> ends in '.meta.'?
    if lastdot < 0:
        base = filename + '.'
    else:
        base = filename[0:lastdot+1]
    # End if
    for extension in _FORTRAN_FILENAME_EXTENSIONS:
        test_name = base + extension
        if os.path.exists(test_name):
            fort_filename = test_name
            break
        # End if
    # End for
    if fort_filename is None:
        raise CCPPError("Cannot find Fortran file associated with {}".format(filename))
    # End if
    return fort_filename

###############################################################################
def create_kinds_file(kind_phys, output_dir, logger):
###############################################################################
    "Create the kinds.F90 file to be used by CCPP schemes and suites"
    kinds_filepath = os.path.join(output_dir, KINDS_FILENAME)
    if logger is not None:
        msg = 'Writing {} to {}'
        logger.info(msg.format(KINDS_FILENAME, output_dir))
    # End if
    with FortranWriter(kinds_filepath, "w") as kindf:
        kindf.write(COPYRIGHT, 0)
        kindf.write(_KINDS_HEADER, 0)
        kindf.write('module {}'.format(KINDS_MODULE), 0)
        kindf.write('', 0)
        use_stmt = 'use ISO_FORTRAN_ENV, only: kind_phys => {}'
        kindf.write(use_stmt.format(kind_phys), 1)
        kindf.write('', 0)
        kindf.write('implicit none', 1)
        kindf.write('private', 1)
        kindf.write('', 0)
        kindf.write('public kind_phys', 1)
        kindf.write('', 0)
        kindf.write('end module {}'.format(KINDS_MODULE), 0)
    # End with
    return kinds_filepath

###############################################################################
def add_error(error_string, new_error):
###############################################################################
    '''Add an error (<new_error>) to <error_string>, separating errors by a
    newline'''
    if error_string:
        error_string += '\n'
    # End if
    return error_string + new_error

###############################################################################
def is_arrayspec(local_name):
###############################################################################
    "Return True iff <local_name> is an array reference"
    return '(' in local_name

###############################################################################
def find_var_in_list(local_name, var_list):
###############################################################################
    """Find a variable, <local_name>, in <var_list>.
    local name is used because Fortran metadata variables do not have
    real standard names.
    Note: The search is case insensitive.
    Return both the variable and the index where it was found.
    If not found, return None for the variable and -1 for the index
    """
    vvar = None
    vind = -1
    lname = local_name.lower()
    for lind, lvar in enumerate(var_list):
        if lvar.get_prop_value('local_name').lower() == lname:
            vvar = lvar
            vind = lind
            break
        # End if
    # End for
    return vvar, vind

###############################################################################
def var_comp(prop_name, mvar, fvar, title, case_sensitive=False):
###############################################################################
    "Compare a property between two variables"
    errors = ''
    mprop = mvar.get_prop_value(prop_name)
    fprop = fvar.get_prop_value(prop_name)
    if not case_sensitive:
        if isinstance(mprop, str):
            mprop = mprop.lower()
        # End if
        if isinstance(fprop, str):
            fprop = fprop.lower()
        # End if
    # End if
    comp = mprop == fprop
    if not comp:
        errmsg = '{} mismatch ({} != {}) in {}{}'
        ctx = context_string(mvar.context)
        errors = add_error(errors,
                           errmsg.format(prop_name, mprop, fprop, title, ctx))
    # End if
    return errors

###############################################################################
def dims_comp(mheader, mvar, fvar, title, logger, case_sensitive=False):
###############################################################################
    "Compare the dimensions attribute of two variables"
    errors = ''
    mdims = mvar.get_dimensions()
    fdims = mheader.convert_dims_to_standard_names(fvar, logger=logger)
    comp = len(mdims) == len(fdims)
    if not comp:
        errmsg = 'Error: rank mismatch in {}/{} ({} != {}){}'
        stdname = mvar.get_prop_value('standard_name')
        ctx = context_string(mvar.context)
        errors = add_error(errors, errmsg.format(title, stdname,
                                                 len(mdims), len(fdims), ctx))
    # End if
    if comp:
        # Now, compare the dims
        for dim_ind, mdim in enumerate(mdims):
            if ':' in mdim:
                mdim = ':'.join([x.strip() for x in mdim.split(':')])
            # End if
            fdim = fdims[dim_ind].strip()
            if ':' in fdim:
                fdim = ':'.join([x.strip() for x in fdim.split(':')])
            # End if
            if not case_sensitive:
                mdim = mdim.lower()
                fdim = fdim.lower()
            # End if
            # Naked colon is okay for Fortran side
            comp = fdim in (':', fdim)
            if not comp:
                errmsg = 'Error: dim {} mismatch ({} != {}) in {}/{}{}'
                stdname = mvar.get_prop_value('standard_name')
                ctx = context_string(mvar.context)
                errmsg = errmsg.format(dim_ind+1, mdim, fdims[dim_ind],
                                       title, stdname, ctx)
                errors = add_error(errors, errmsg)
            # End if
        # End for
    # End if
    return errors

###############################################################################
def compare_fheader_to_mheader(meta_header, fort_header, logger):
###############################################################################
    """Compare a metadata header against the header generated from the
    corresponding code in the associated Fortran file.
    Return a string with any errors found (empty string is no errors).
    """
    errors_found = ''
    title = meta_header.title
    mht = meta_header.header_type
    fht = fort_header.header_type
    if mht != fht:
        # Special case, host metadata can be in a Fortran module or scheme
        if (mht != 'host') or (fht not in ('module', 'scheme')):
            errmsg = 'Metadata table type mismatch for {}, {} != {}{}'
            ctx = meta_header.start_context()
            raise CCPPError(errmsg.format(title, meta_header.header_type,
                                          fort_header.header_type, ctx))
        # End if
    else:
        # The headers should have the same variables in the same order
        # The exception is that a Fortran module can have variable declarations
        # after all the metadata variables.
        mlist = meta_header.variable_list()
        mlen = len(mlist)
        flist = fort_header.variable_list()
        flen = len(flist)
        # Remove array references from mlist before checking lengths
        for mvar in mlist:
            if is_arrayspec(mvar.get_prop_value('local_name')):
                mlen -= 1
            # End if
        # End for
        list_match = mlen == flen
        if not list_match:
            if fht in _EXTRA_VARIABLE_TABLE_TYPES:
                if flen > mlen:
                    list_match = True
                else:
                    etype = 'Fortran {}'.format(fht)
                # End if
            elif flen > mlen:
                etype = 'metadata header'
            else:
                etype = 'Fortran {}'.format(fht)
            # End if
        # End if
        if not list_match:
            errmsg = 'Variable mismatch in {}, variables missing from {}.'
            errors_found = add_error(errors_found, errmsg.format(title, etype))
        # End if
        for mind, mvar in enumerate(mlist):
            lname = mvar.get_prop_value('local_name')
            arrayref = is_arrayspec(lname)
            fvar, find = find_var_in_list(lname, flist)
            if mind >= flen:
                if arrayref:
                    # Array reference, variable not in Fortran table
                    pass
                elif fvar is None:
                    errmsg = 'No Fortran variable for {} in {}'
                    errors_found = add_error(errors_found,
                                             errmsg.format(lname, title))
                # End if (no else, we already reported an out-of-place error
                # Do not break to collect all missing variables
                continue
            # End if
            # At this point, we should have a Fortran variable
            if fvar is None:
                errmsg = 'Variable mismatch in {}, no Fortran variable {}.'
                errors_found = add_error(errors_found, errmsg.format(title,
                                                                     lname))
                continue
            # End if
            # Check order dependence
            if fht in _ORDERED_TABLE_TYPES:
                if find != mind:
                    errmsg = 'Out of order argument, {} in {}'
                    errors_found = add_error(errors_found,
                                             errmsg.format(lname, title))
                    continue
                # End if
            # End if
            if arrayref:
                # Array reference, do not look for this in Fortran table
                continue
            # End if
            errs = var_comp('local_name', mvar, fvar, title)
            if errs:
                errors_found = add_error(errors_found, errs)
            else:
                errs = var_comp('type', mvar, fvar, title)
                if errs:
                    errors_found = add_error(errors_found, errs)
                # End if
                errs = var_comp('kind', mvar, fvar, title)
                if errs:
                    errors_found = add_error(errors_found, errs)
                # End if
                if meta_header.header_type == 'scheme':
                    errs = var_comp('intent', mvar, fvar, title)
                    if errs:
                        errors_found = add_error(errors_found, errs)
                    # End if
                # End if
                # Compare dimensions
                errs = dims_comp(meta_header, mvar, fvar, title, logger)
                if errs:
                    errors_found = add_error(errors_found, errs)
                # End if
            # End if
        # End for
    # End if
    return errors_found

###############################################################################
def check_fortran_against_metadata(meta_headers, fort_headers,
                                   mfilename, ffilename, logger):
###############################################################################
    """Compare a set of metadata headers from <mfilename> against the
    code in the associated Fortran file, <ffilename>.
    NB: This routine destroys the list, <fort_headers> but returns the
       contents in an association dictionary on successful completion."""
    header_dict = {} # Associate a Fortran header for every metadata header
    for mheader in meta_headers:
        fheader = None
        mtitle = mheader.title
        for findex in range(len(fort_headers)): #pylint: disable=consider-using-enumerate
            if fort_headers[findex].title == mtitle:
                fheader = fort_headers.pop(findex)
                break
            # End if
        # End for
        if fheader is None:
            tlist = '\n    '.join([x.title for x in fort_headers])
            logger.debug("CCPP routines in {}:{}".format(ffilename, tlist))
            errmsg = "No matching Fortran routine found for {} in {}"
            raise CCPPError(errmsg.format(mtitle, ffilename))
        # End if
        header_dict[mheader] = fheader
        # End if
    # End while
    if fort_headers:
        errmsg = ""
        sep = ""
        for fheader in fort_headers:
            if fheader.has_variables:
                errmsg += sep + "No matching metadata header found for {} in {}"
                errmsg = errmsg.format(fheader.title, mfilename)
                sep = "\n"
            # End if
        # End for
        if errmsg:
            raise CCPPError(errmsg)
        # End if
    # End if
    # We have a one-to-one set, compare headers
    errors_found = ''
    for mheader in header_dict:
        fheader = header_dict[mheader]
        errors_found += compare_fheader_to_mheader(mheader, fheader, logger)
    # End for
    if errors_found:
        num_errors = len(re.findall(r'\n', errors_found)) + 1
        errmsg = "{}\n{} error{} found comparing {} to {}"
        raise CCPPError(errmsg.format(errors_found, num_errors,
                                      's' if num_errors > 1 else '',
                                      mfilename, ffilename))
    # End if
    # No return, an exception is raised on error

###############################################################################
def parse_host_model_files(host_filenames, preproc_defs, host_name, logger):
###############################################################################
    """
    Gather information from host files (e.g., DDTs, registry) and
    return a host model object with the information.
    """
    meta_headers = {}
    known_ddts = list()
    for filename in host_filenames:
        logger.info('Reading host model data from {}'.format(filename))
        # parse metadata file
        mheaders = MetadataTable.parse_metadata_file(filename, known_ddts,
                                                     logger)
        fort_file = find_associated_fortran_file(filename)
        fheaders = parse_fortran_file(fort_file, preproc_defs=preproc_defs,
                                      logger=logger)
        # Check Fortran against metadata (will raise an exception on error)
        check_fortran_against_metadata(mheaders, fheaders,
                                       filename, fort_file, logger)
        # Check for duplicates, then add to dict
        for header in mheaders:
            if header.title not in meta_headers:
                meta_headers[header.title] = header
                if header.header_type == 'ddt':
                    known_ddts.append(header.title)
            else:
                errmsg = "Duplicate {typ}, {title}, found in {file}"
                edict = {'title':header.title,
                         'file':filename,
                         'typ':header.header_type}
                oheader = meta_headers[header.title]
                ofile = oheader.context.filename
                if ofile is not None:
                    errmsg = errmsg + ", original found in {ofile}"
                    edict['ofile'] = ofile
                # End if
                raise CCPPError(errmsg.format(**edict))
                # End if
            # End if
        # End for
    # End for
    if not host_name:
        host_name = None
    # End if
    host_model = HostModel(meta_headers.values(), host_name, logger)
    return host_model

###############################################################################
def parse_scheme_files(scheme_filenames, preproc_defs, logger):
###############################################################################
    """
    Gather information from scheme files (e.g., init, run, and finalize
    methods) and return resulting dictionary.
    """
    meta_headers = list()
    header_dict = {} # To check for duplicates
    known_ddts = list()
    for filename in scheme_filenames:
        logger.info('Reading CCPP schemes from {}'.format(filename))
        # parse metadata file
        mheaders = MetadataTable.parse_metadata_file(filename, known_ddts,
                                                     logger)
        fort_file = find_associated_fortran_file(filename)
        fheaders = parse_fortran_file(fort_file, preproc_defs=preproc_defs,
                                      logger=logger)
        # Check Fortran against metadata (will raise an exception on error)
        check_fortran_against_metadata(mheaders, fheaders,
                                       filename, fort_file, logger)
        # Check for duplicates, then add to dict
        for header in mheaders:
            if header.title in header_dict:
                errmsg = "Duplicate {ttype}, {title}, found in {file}"
                edict = {'title':header.title,
                         'file':filename,
                         'ttype':header.header_type}
                oheader = header_dict[header.title]
                ofile = oheader.context.filename
                if ofile is not None:
                    errmsg = errmsg + ", original found in {ofile}"
                    edict['ofile'] = ofile
                # End if
                raise CCPPError(errmsg.format(**edict))
            # End if
            meta_headers.append(header)
            header_dict[header.title] = header
            if header.header_type == 'ddt':
                known_ddts.append(header.title)
            # End if
        # End for
    # End for
    return meta_headers

###############################################################################
def clean_capgen(cap_output_file, logger):
###############################################################################
    """Attempt to remove the files created by the last invocation of capgen"""
    log_level = logger.getEffectiveLevel()
    set_log_level(logger, logging.INFO)
    if os.path.exists(cap_output_file):
        logger.info("Cleaning capgen files from {}".format(cap_output_file))
        delete_pathnames_from_file(cap_output_file, logger)
    else:
        emsg = "Unable to run clean, {} not found"
        logger.error(emsg.format(cap_output_file))
    # End if
    set_log_level(logger, log_level)

###############################################################################
def capgen(host_files, scheme_files, suites, datatable_file, preproc_defs,
           gen_hostcap, gen_docfiles, output_dir, host_name, kind_phys, logger):
###############################################################################
    """Parse indicated host, scheme, and suite files.
    Generate code to allow host model to run indicated CCPP suites."""
    # We need to create three lists of files, hosts, schemes, and SDFs
    host_files = create_file_list(host_files, ['meta'], 'Host', logger)
    scheme_files = create_file_list(scheme_files, ['meta'], 'Scheme', logger)
    sdfs = create_file_list(suites, ['xml'], 'Suite', logger)
    check_for_writeable_file(datatable_file, "Cap output datatable")
    ##XXgoldyXX: Temporary warning
    if gen_docfiles:
        raise CCPPError("--generate-docfiles not yet supported")
    # End if
    # First up, handle the host files
    host_model = parse_host_model_files(host_files, preproc_defs,
                                        host_name, logger)
    # Next, parse the scheme files
    scheme_headers = parse_scheme_files(scheme_files, preproc_defs, logger)
    ddts = host_model.ddt_lib.keys()
    if ddts:
        logger.debug("DDT definitions = {}".format(ddts))
    # End if
    plist = host_model.prop_list('local_name')
    logger.debug("{} variables = {}".format(host_model.name, plist))
    logger.debug("schemes = {}".format([x.title for x in scheme_headers]))
    # Finally, we can get on with writing suites
    ccpp_api = API(sdfs, host_model, scheme_headers, logger)
    cap_filenames = ccpp_api.write(output_dir, logger)
    if gen_hostcap:
        # Create a cap file
        hcap_filename = write_host_cap(host_model, ccpp_api,
                                       output_dir, logger)
    else:
        hcap_filename = None
    # End if
    # Create the kinds file
    kinds_file = create_kinds_file(kind_phys, output_dir, logger)
    # Finally, create the database of generated files and caps
    generate_ccpp_datatable(datatable_file, ccpp_api, [hcap_filename],
                            cap_filenames, kinds_file)

###############################################################################
def _main_func():
###############################################################################
    """Parse command line, then parse indicated host, scheme, and suite files.
    Finally, generate code to allow host model to run indicated CCPP suites."""
    args = parse_command_line(sys.argv[1:], __doc__)
    verbosity = args.verbose
    if verbosity > 1:
        set_log_level(_LOGGER, logging.DEBUG)
    elif verbosity > 0:
        set_log_level(_LOGGER, logging.INFO)
    # End if
    # Make sure we know where output is going
    output_dir = os.path.abspath(args.output_root)
    if os.path.abspath(args.ccpp_datafile):
        datatable_file = args.ccpp_datafile
    else:
        datatable_file = os.path.abspath(os.path.join(output_dir,
                                                       args.ccpp_datafile))
    # End if
    ## A few sanity checks
    ## Make sure output directory is legit
    if os.path.exists(output_dir):
        if not os.path.isdir(output_dir):
            errmsg = "output-root, '{}', is not a directory"
            raise CCPPError(errmsg.format(args.output_root))
        # End if
        if not os.access(output_dir, os.W_OK):
            errmsg = "Cannot write files to output-root ({})"
            raise CCPPError(errmsg.format(args.output_root))
        # End if (output_dir is okay)
    else:
        # Try to create output_dir (let it crash if it fails)
        os.makedirs(output_dir)
    # End if
    # Make sure we can create output file lists
    if not os.path.isabs(datatable_file):
        datatable_file = os.path.normpath(os.path.join(output_dir,
                                                        datatable_file))
    # End if
    if args.clean:
        clean_capgen(datatable_file, _LOGGER)
    else:
        generate_host_cap = args.host_name != ''
        capgen(args.host_files, args.scheme_files, args.suites, datatable_file,
               args.preproc_directives, generate_host_cap,
               args.generate_docfiles, output_dir, args.host_name,
               args.kind_phys, _LOGGER)
    # End if (clean)

###############################################################################

if __name__ == "__main__":
    try:
        _main_func()
        sys.exit(0)
    except ParseInternalError as pie:
        _LOGGER.exception(pie)
        sys.exit(-1)
    except CCPPError as ccpp_err:
        if _LOGGER.getEffectiveLevel() <= logging.DEBUG:
            _LOGGER.exception(ccpp_err)
        else:
            _LOGGER.error(ccpp_err)
        # End if
        sys.exit(1)
    finally:
        logging.shutdown()
    # End try
