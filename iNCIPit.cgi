#! /usr/bin/perl 

#
# Copyleft 2014 Jon Scott <mr.jonathon.scott@gmail.com> 
# Copyleft 2014 Mark Cooper <mark.c.cooper@outlook.com> 
# Copyright 2012-2013 Midwest Consortium for Library Services
# Copyright 2013 Calvin College
#     contact Dan Wells <dbw2@calvin.edu>
# Copyright 2013 Traverse Area District Library,
#     contact Jeff Godin <jgodin@tadl.org>
#
#
# This code incorporates code (with modifications) from issa, "a small
# command-line client to OpenILS/Evergreen". issa is licensed GPLv2 or (at your
# option) any later version of the GPL.
#
# issa is copyright:
#
# Copyright 2011 Jason J.A. Stephenson <jason@sigio.com>
# Portions Copyright 2012 Merrimack Valley Library Consortium
# <jstephenson@mvlc.org>
#
#
# This file is part of iNCIPit
#
# iNCIPit is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# iNCIPit is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public
# License for more details.
#
# You should have received a copy of the GNU General Public License
# along with iNCIPit. If not, see <http://www.gnu.org/licenses/>.

use warnings;
use strict;
use XML::LibXML;
use XML::LibXML::ErrNo;
use CGI;
use HTML::Entities;
use CGI::Carp;
use OpenSRF::System;
use OpenSRF::Utils::SettingsClient;
use Digest::MD5 qw/md5_hex/;
use OpenILS::Utils::Fieldmapper;
use OpenILS::Utils::CStoreEditor qw/:funcs/;
use OpenILS::Const qw/:const/;
use Scalar::Util qw(reftype blessed);
use MARC::Record;
use MARC::Field;
use MARC::File::XML;
use POSIX qw/strftime/;
use DateTime;
use Config::Tiny;

my $U = "OpenILS::Application::AppUtils";

my $cgi = CGI->new();
my $xml = $cgi->param('POSTDATA');# || $cgi->param('XForms:Model');
my $host = $cgi->url(-base=>1);
my $hostname = (split "/", $host)[2]; # base hostname i.e. www.example.org
my $conffile = "$hostname.ini"; # hostname specific ini file i.e. www.example.org.ini
my $conf;

# attempt to load configuration file using matching request hostname, fallback to default
if (-e $conffile) {
        $conf = load_config($conffile);
} else {
        $conffile = "/openils/conf/ncip/ncip.ini";
        $conf = load_config($conffile);
}

# Set some variables from config (or defaults)

# Default patron_identifier type is barcode
my $patron_id_type = "barcode";

# if the config specifies a patron_identifier type
if (my $conf_patron_id_type = $conf->{behavior}->{patron_identifier}) {
    # and that patron_identifier type is known
    if ($conf_patron_id_type =~ m/(barcode|id)/) {
        # override the default with the value from the config
        $patron_id_type = $conf_patron_id_type;
    }
}

# reject non-https access unless configured otherwise
unless ($conf->{access}->{permit_plaintext} =~ m/^yes$/i) {
    unless (defined($ENV{HTTPS}) && $ENV{HTTPS} eq 'on') {
        print "Content-type: text/plain\n\n";
        print "Access denied.\n";
        exit 0;
    }
}

# TODO: support for multiple load balancer IPs
my $lb_ip = $conf->{access}->{load_balancer_ip};

# if we are behind a load balancer, check to see that the
# actual client IP is permitted
if ($lb_ip) {
    my @allowed_ips = split(/ *, */, $conf->{access}->{allowed_client_ips});

    my $forwarded = $ENV{HTTP_X_FORWARDED_FOR};
    my $ok = 0;

    foreach my $check_ip (@allowed_ips) {
        $ok = 1 if ($check_ip eq $forwarded);
    }

    # if we have a load balancer IP and are relying on
    # X-Forwarded-For, deny requests other than those
    # from the load balancer
    # TODO: support for chained X-Forwarded-For -- ignore all but last
    unless ($ok && $ENV{REMOTE_ADDR} eq $lb_ip) {
        print "Content-type: text/plain\n\n";
        print "Access denied.\n";
        exit 0;
    }
}


# log request hostname, configuration file used and posted data
# XXX: posted ncip message log filename should be in config.
open (POST_DATA, ">>/openils/var/log/post_data.txt") or die "Cannot write post_data.txt";
print POST_DATA "INCOMING REQUEST\t$hostname\n";
print POST_DATA "CONFIGURATION FILE\t$conffile\n";
print POST_DATA "$xml\n";
close POST_DATA;

# initialize the parser
my $parser = new XML::LibXML;
my $doc;

# Attempt to parse XML without any modification
eval {
    $doc = $parser->load_xml( string => $xml );
};

# Attempt to gracefully handle invalid XML, including mitigations for known common issues.
if ($@ && ref($@) ne 'XML::LibXML::Error') {
    # We received an error, but it was not a LibXML error object
    fail("Unknown error parsing XML: $@");
} elsif ($@) {
    # We received an error in the form of a LibXML error object

    my $warning = sprintf("Unable to parse XML on the first try. LibXML error code: %s, message: %s", $@->code(), $@->message());
    warn $warning;

    # If the error was ERR_INVALID_CHAR, attempt to modify XML and try again
    if ($@->code() == XML::LibXML::ErrNo::ERR_INVALID_CHAR) {

        warn "Attempting to de-mangle by removing known invalid character(s).\n";

        # This is based on actual invalid XML encountered in the wild
        # in an INN-REACH environment.
        $xml =~ s/\x04//g; # Remove ^D from xml

        # Attempt to re-parse after de-mangling
        eval {
            $doc = $parser->load_xml( string => $xml );
        };

        if ($@ && ref($@) ne 'XML::LibXML::Error') {
            # We received an error, but it was not a LibXML error object
            fail("Unknown error parsing XML on second attempt: $@");
        } elsif ($@) {
            # We received an error in the form of a LibXML error object
            my $error = sprintf("Unable to parse XML even after de-mangling. LibXML error code: %s, message: %s", $@->code(), $@->message());
            fail($error);
        }
        warn "Success parsing XML after de-mangling.\n";
    } else {
        # This is not an error that we know how to recover from
        fail("No known workaround for this error. Giving up.") unless $doc;
    }
}

fail("XML parsing did not result in a document.") unless $doc && ref($doc) eq 'XML::LibXML::Document';

my %session = login();

if ( defined( $session{authtoken} ) ) {
    $doc->exists('/NCIPMessage/LookupUser')           ? lookupUser()       : (
    $doc->exists('/NCIPMessage/ItemRequested')        ? item_request()     : (
    $doc->exists('/NCIPMessage/ItemShipped')          ? item_shipped()     : (
    $doc->exists('/NCIPMessage/ItemCheckedOut')       ? item_checked_out() : (
    $doc->exists('/NCIPMessage/CheckOutItem')         ? check_out_item()   : (
    $doc->exists('/NCIPMessage/ItemCheckedIn')        ? item_checked_in()  : (
    $doc->exists('/NCIPMessage/CheckInItem')          ? check_in_item()    : (
    $doc->exists('/NCIPMessage/ItemReceived')         ? item_received()    : (
    $doc->exists('/NCIPMessage/AcceptItem')           ? accept_item()      : (
    $doc->exists('/NCIPMessage/ItemRequestCancelled') ? item_cancelled()   : (
    $doc->exists('/NCIPMessage/ItemRenewed')          ? item_renew()       : (
    $doc->exists('/NCIPMessage/RenewItem')            ? renew_item()       :
    fail("UNKNOWN NCIPMessage")
    )))))))))));

    logout();
} else {
    fail("Unable to perform action : Unknown Service Request");
}

# load and parse config file
sub load_config {
    my $file = shift;

    my $Config = Config::Tiny->new;
    $Config = Config::Tiny->read( $file ) ||
        die( "Error reading config file ", $file, ": ", Config::Tiny->errstr, "\n" );
    return $Config;
}

# load and parse userpriv_map file, returning a hashref
sub load_map_file {
    my $filename = shift;
    my $map = {};
    if (open(my $fh, "<", $filename)) {
        while (my $entry = <$fh>) {
            chomp($entry);
            my ($from, $to) = split(m/:/, $entry);
            $map->{$from} = $to;
        }
        close $fh;
    }
    return $map;
}

sub lookup_userpriv {
    my $input = shift;
    my $map = shift;
    if (defined($map->{$input})) { # if we have a mapping for this profile
        return $map->{$input}; # return value from mapping hash
    } else {
        return $input; # return original value
    }
}

sub lookup_pickup_lib {
    my $input = shift;
    my $map = shift;
    if (defined($map->{$input})) { # if we found this pickup lib
        return $map->{$input}; # return value from mapping hash
    } else {
        return undef; # the original value does us no good -- return undef
    }
}

sub logit {
    my ( $msg, $func, $more_info ) = @_;
    open (RESP_DATA, ">>/openils/var/log/resp_data.txt") or die "Cannot write resp_data.txt";
    print RESP_DATA $msg;
    print RESP_DATA $more_info unless !$more_info;
    close RESP_DATA;
    print $msg || fail($func);
}

sub staff_log {
    my ( $taiv, $faiv, $more_info ) = @_;
    my $now = localtime();
    open (STAFF_LOG, ">>/openils/var/log/ncip_log.txt") or die "Cannot write staff_data.csv";
    print STAFF_LOG "$now, $faiv, $taiv, $more_info\n";
    close STAFF_LOG;
}

sub item_renew {
    my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemRenewed/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    my $faidScheme = HTML::Entities::encode($faidSchemeX);
    my $faidValue  = $doc->find('/NCIPMessage/ItemRenewed/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');
    my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemRenewed/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    my $taidScheme = HTML::Entities::encode($taidSchemeX);
    my $taidValue  = $doc->find('/NCIPMessage/ItemRenewed/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');

    my $pid = $doc->findvalue('/NCIPMessage/ItemRenewed/UniqueUserId/UserIdentifierValue');
    my $visid = $doc->findvalue('/NCIPMessage/ItemRenewed/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier') . $faidValue;
    my $due_date = $doc->findvalue('/NCIPMessage/ItemRenewed/DateDue');

    my $r = renewal( $visid, $due_date );

    my $hd = <<ITEMRENEWAL;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <ItemRenewedResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$faidScheme</Scheme>
                    <Value>$faidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <UniqueItemId>
            <ItemIdentifierValue datatype="string">$visid</ItemIdentifierValue>
        </UniqueItemId>
    </ItemRenewedResponse>
</NCIPMessage> 

ITEMRENEWAL

    my $more_info = <<MOREINFO;

VISID             = $visid
Desired Due Date     = $due_date

MOREINFO

    logit( $hd, ( caller(0) )[3], $more_info );
    staff_log( $taidValue, $faidValue,
            "ItemRenewal -> Patronid : "
          . $pid
          . " | Visid : "
          . $visid
          . " | Due Date : "
          . $due_date );
}

sub renew_item {
    my $faidSchemeX = $doc->findvalue('/NCIPMessage/RenewItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    my $faidScheme = HTML::Entities::encode($faidSchemeX);
    my $faidValue  = $doc->find('/NCIPMessage/RenewItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');
    my $taidSchemeX = $doc->findvalue('/NCIPMessage/RenewItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    my $taidScheme = HTML::Entities::encode($taidSchemeX);
    my $taidValue  = $doc->find('/NCIPMessage/RenewItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');

    my $pid = $doc->findvalue('/NCIPMessage/RenewItem/UniqueUserId/UserIdentifierValue');
    my $unique_item_id = $doc->findvalue('/NCIPMessage/RenewItem/UniqueItemId/ItemIdentifierValue');
    my $due_date = $doc->findvalue('/NCIPMessage/RenewItem/DesiredDateDue');

    # we are using the UniqueItemId value as a barcode here
    my $r = renewal( $unique_item_id, $due_date );

    my $hd = <<ITEMRENEWAL;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <RenewItemResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$faidScheme</Scheme>
                    <Value>$faidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <UniqueItemId>
            <ItemIdentifierValue datatype="string">$unique_item_id</ItemIdentifierValue>
        </UniqueItemId>
    </RenewItemResponse>
</NCIPMessage> 

ITEMRENEWAL

    my $more_info = <<MOREINFO;

UNIQUEID             = $unique_item_id
Desired Due Date     = $due_date

MOREINFO

    logit( $hd, ( caller(0) )[3], $more_info );
    staff_log( $taidValue, $faidValue,
            "RenewItem -> Patronid : "
          . $pid
          . " | Uniqueid: : "
          . $unique_item_id
          . " | Due Date : "
          . $due_date );
}

sub accept_item {
    my $faidSchemeX = $doc->findvalue('/NCIPMessage/AcceptItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    my $faidScheme = HTML::Entities::encode($faidSchemeX);
    my $faidValue  = $doc->find('/NCIPMessage/AcceptItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');
    my $taidSchemeX = $doc->findvalue('/NCIPMessage/AcceptItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    my $taidScheme = HTML::Entities::encode($taidSchemeX);
    my $taidValue  = $doc->find('/NCIPMessage/AcceptItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');
    my $visid = $doc->findvalue('/NCIPMessage/AcceptItem/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier') . $faidValue;
    my $request_id = $doc->findvalue('/NCIPMessage/AcceptItem/UniqueRequestId/RequestIdentifierValue') || "unknown";
    my $patron = $doc->findvalue('/NCIPMessage/AcceptItem/UserOptionalFields/VisibleUserId/VisibleUserIdentifier');
    my $copy = copy_from_barcode($visid);
    fail( "accept_item: " . $copy->{textcode} . " $visid" ) unless ( blessed $copy);
    my $r2 = update_copy( $copy, $conf->{status}->{hold} ); # put into INN-Reach Hold status
    # We need to find the hold to know the pickup location
    my $hold = find_hold_on_copy($visid);
    if (defined $hold && blessed($hold)) {
        # Check the copy in to capture for hold -- do it at the pickup_lib
        # so that the hold becomes Available
        my $checkin_result = checkin_accept($copy->id, $hold->pickup_lib);
    } else {
        fail( "accept_item: no hold found for visid " . $visid );
    }

    my $hd = <<ACCEPTITEM;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <AcceptItemResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$faidScheme</Scheme>
                    <Value>$faidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
    <UniqueRequestId>
            <ItemIdentifierValue datatype="string">$request_id</ItemIdentifierValue>
        </UniqueRequestId>
        <UniqueItemId>
            <ItemIdentifierValue datatype="string">$visid</ItemIdentifierValue>
        </UniqueItemId>
    </AcceptItemResponse>
</NCIPMessage> 

ACCEPTITEM

    logit( $hd, ( caller(0) )[3] );
    staff_log( $taidValue, $faidValue,
        "AcceptItem -> Request Id : " . $request_id . " | Patron Id : " . $patron . " | Visible Id :" . $visid );
}

sub item_received {
    my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemReceived/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    my $faidScheme = HTML::Entities::encode($faidSchemeX);
    my $faidValue = $doc->find('/NCIPMessage/ItemReceived/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');
    my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemReceived/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    my $taidScheme = HTML::Entities::encode($taidSchemeX);
    my $taidValue  = $doc->find('/NCIPMessage/ItemReceived/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');
    my $visid = $doc->findvalue('/NCIPMessage/ItemReceived/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier') . $faidValue;
    my $copy = copy_from_barcode($visid);
    fail( $copy->{textcode} . " $visid" ) unless ( blessed $copy);
    my $r1 = checkin($visid);# if ( $copy->status == OILS_COPY_STATUS_CHECKED_OUT ); # checkin the item before delete if ItemCheckedIn step was skipped
    my $r2 = delete_copy($copy);

    my $hd = <<ITEMRECEIVED;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <ItemReceivedResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$faidScheme</Scheme>
                    <Value>$faidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <UniqueItemId>
            <ItemIdentifierValue datatype="string">$visid</ItemIdentifierValue>
        </UniqueItemId>
    </ItemReceivedResponse>
</NCIPMessage> 

ITEMRECEIVED

    logit( $hd, ( caller(0) )[3] );
    staff_log( $taidValue, $faidValue, "ItemReceived -> Visible ID : " . $visid );
}

sub item_cancelled {
    my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    my $faidScheme = HTML::Entities::encode($faidSchemeX);
    my $faidValue  = $doc->find('/NCIPMessage/ItemRequestCancelled/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');

    my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    my $taidScheme = HTML::Entities::encode($taidSchemeX);
    my $taidValue  = $doc->find('/NCIPMessage/ItemRequestCancelled/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');
    my $UniqueItemIdAgencyIdValue = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/UniqueItemId/UniqueAgencyId/Value');

    my $barcode = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/UniqueItemId/ItemIdentifierValue');

    if ( $barcode =~ /^i/ ) {    # delete copy only if barcode is an iNUMBER
        $barcode .= $faidValue;
        my $copy = copy_from_barcode($barcode);
        fail( $copy->{textcode} . " $barcode" ) unless ( blessed $copy);
        my $r = delete_copy($copy);
    } else {
        # remove hold!
        my $r = cancel_hold($barcode);
        # TODO: check for any errors or unexpected return values in $r
        my $copy = copy_from_barcode($barcode);
        fail( $copy->{textcode} . " $barcode" ) unless ( blessed $copy);
        $r = update_copy( $copy, 7 ); # set to reshelving (for wiggle room)
        # TODO: check for any errors or unexpected return values in $r
# XXX other options here could be:
# - Set to 'available' (it is probably still on the shelf, though it might be in the process of being retrieved)
# - Use checkin() here instead - This could trigger things we don't want to happen, though the 'noop' flag should catch at least some of that
#
# Also, presumably they cannot cancel once the item is in transit?  If they can, we'll need more logic to decide what to do here.
    }

    my $hd = <<ITEMREQUESTCANCELLED;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <ItemRequestCancelledResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$faidScheme</Scheme>
                    <Value>$faidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <UniqueItemId>
            <ItemIdentifierValue datatype="string">$barcode</ItemIdentifierValue>
        </UniqueItemId>
    </ItemRequestCancelledResponse>
</NCIPMessage> 

ITEMREQUESTCANCELLED

    logit( $hd, ( caller(0) )[3] );
    staff_log( $taidValue, $faidValue,
        "ItemRequestCancelled -> Barcode : " . $barcode );
}

sub item_checked_in {
    my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedIn/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    my $faidScheme = HTML::Entities::encode($faidSchemeX);
    my $faidValue  = $doc->find('/NCIPMessage/ItemCheckedIn/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');
    my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedIn/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    my $taidScheme = HTML::Entities::encode($taidSchemeX);
    my $taidValue  = $doc->find('/NCIPMessage/ItemCheckedIn/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');

    my $visid = $doc->findvalue('/NCIPMessage/ItemCheckedIn/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier') . $faidValue;
    my $r = checkin($visid);
    my $copy = copy_from_barcode($visid);
    fail( $copy->{textcode} . " $visid" ) unless ( blessed $copy);
    my $r2 = update_copy( $copy, $conf->{status}->{transit_return} ); # "INN-Reach Transit Return" status

    my $hd = <<ITEMCHECKEDIN;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <ItemCheckedInResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$faidScheme</Scheme>
                    <Value>$faidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <UniqueItemId>
            <ItemIdentifierValue datatype="string">$visid</ItemIdentifierValue>
        </UniqueItemId>
    </ItemCheckedInResponse>
</NCIPMessage> 

ITEMCHECKEDIN

    logit( $hd, ( caller(0) )[3] );
    staff_log( $taidValue, $faidValue, "ItemCheckedIn -> Visible ID : " . $visid );
}

sub item_checked_out {
    my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedOut/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    my $faidScheme = HTML::Entities::encode($faidSchemeX);
    my $faidValue  = $doc->find('/NCIPMessage/ItemCheckedOut/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');
    my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedOut/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    my $taidScheme = HTML::Entities::encode($taidSchemeX);
    my $taidValue  = $doc->find('/NCIPMessage/ItemCheckedOut/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');

    my $patron_barcode = $doc->findvalue('/NCIPMessage/ItemCheckedOut/UserOptionalFields/VisibleUserId/VisibleUserIdentifier');
    my $due_date = $doc->findvalue('/NCIPMessage/ItemCheckedOut/DateDue');
    my $visid = $doc->findvalue('/NCIPMessage/ItemCheckedOut/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier') . $faidValue;

    my $copy = copy_from_barcode($visid);
    fail( $copy->{textcode} . " $visid" ) unless ( blessed $copy);
    my $r = update_copy( $copy, 0 ); # seemed like copy had to be available before it could be checked out, so ...
    my $r1 = checkin($visid) if ( $copy->status == OILS_COPY_STATUS_CHECKED_OUT ); # double posted itemcheckedout messages cause error ... trying to simplify
    my $r2 = checkout( $visid, $patron_barcode, $due_date );

    my $hd = <<ITEMCHECKEDOUT;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <ItemCheckedOutResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$faidScheme</Scheme>
                    <Value>$faidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <UniqueItemId>
            <ItemIdentifierValue datatype="string">$visid</ItemIdentifierValue>
        </UniqueItemId>
    </ItemCheckedOutResponse>
</NCIPMessage> 

ITEMCHECKEDOUT

    logit( $hd, ( caller(0) )[3] );
    staff_log( $taidValue, $faidValue,
        "ItemCheckedOut -> Visible Id : " . $visid . " | Patron Barcode : " . $patron_barcode . " | Due Date : " . $due_date );
}

sub check_out_item {
    my $faidSchemeX = $doc->findvalue('/NCIPMessage/CheckOutItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    my $faidScheme = HTML::Entities::encode($faidSchemeX);
    my $faidValue  = $doc->find('/NCIPMessage/CheckOutItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');
    my $taidSchemeX = $doc->findvalue('/NCIPMessage/CheckOutItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    my $taidScheme = HTML::Entities::encode($taidSchemeX);
    my $taidValue  = $doc->find('/NCIPMessage/CheckOutItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');

    my $mdate = $doc->findvalue('/NCIPMessage/CheckOutItem/MandatedAction/DateEventOccurred');
    # TODO: look up individual accounts for agencies based on barcode prefix + agency identifier
    my $patron_barcode = $conf->{checkout}->{institutional_patron}; # patron id if patron_id_as_identifier = yes

    # For CheckOutItem and INN-REACH, this value will correspond with our local barcode
    my $barcode = $doc->findvalue('/NCIPMessage/CheckOutItem/UniqueItemId/ItemIdentifierValue');

    # TODO: watch for possible real ids here?
    my $due_date = $doc->findvalue('/NCIPMessage/CheckOutItem/DateDue');

    my $copy = copy_from_barcode($barcode);
    fail( $copy->{textcode} . " $barcode" ) unless ( blessed $copy);

    my $r2 = checkout( $barcode, $patron_barcode, $due_date );

    # TODO: check for checkout exception (like OPEN_CIRCULATION_EXISTS)

    my $hd = <<CHECKOUTITEM;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <CheckOutItemResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$faidScheme</Scheme>
                    <Value>$faidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <UniqueItemId>
            <ItemIdentifierValue datatype="string">$barcode</ItemIdentifierValue>
        </UniqueItemId>
    </CheckOutItemResponse>
</NCIPMessage> 

CHECKOUTITEM

    logit( $hd, ( caller(0) )[3] );
    staff_log( $taidValue, $faidValue,
        "CheckOutItem -> Barcode : " . $barcode . " | Patron Barcode : " . $patron_barcode . " | Due Date : " . $due_date );
}

sub check_in_item {
    my $faidSchemeX = $doc->findvalue('/NCIPMessage/CheckInItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    my $faidScheme = HTML::Entities::encode($faidSchemeX);
    my $faidValue  = $doc->find('/NCIPMessage/CheckInItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');
    my $taidSchemeX = $doc->findvalue('/NCIPMessage/CheckInItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    my $taidScheme = HTML::Entities::encode($taidSchemeX);
    my $taidValue  = $doc->find('/NCIPMessage/CheckInItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');

    # For CheckInItem and INN-REACH, this value will correspond with our local barcode
    my $barcode = $doc->findvalue('/NCIPMessage/CheckInItem/UniqueItemId/ItemIdentifierValue');
    my $r = checkin($barcode, 1);
    fail($r) if $r =~ /^COPY_NOT_CHECKED_OUT/;
    # TODO: do we need to do these next steps?  checkin() should handle everything, and we want this to end up in 'reshelving'.  If we are worried about transits, we should handle (abort) them, not just change the status
    ##my $copy = copy_from_barcode($barcode);
    ##fail($copy->{textcode}." $barcode") unless (blessed $copy);
    ## 	my $r2 = update_copy($copy,0); # Available now 

    my $hd = <<CHECKINITEM;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <CheckInItemResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$faidScheme</Scheme>
                    <Value>$faidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <UniqueItemId>
            <ItemIdentifierValue datatype="string">$barcode</ItemIdentifierValue>
        </UniqueItemId>
    </CheckInItemResponse>
</NCIPMessage> 

CHECKINITEM

    logit( $hd, ( caller(0) )[3] );
    staff_log( $taidValue, $faidValue, "CheckInItem -> Barcode : " . $barcode );
}

sub item_shipped {
    my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemShipped/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    my $faidScheme = HTML::Entities::encode($faidSchemeX);
    my $faidValue  = $doc->find('/NCIPMessage/ItemShipped/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');
    my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemShipped/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    my $taidScheme = HTML::Entities::encode($taidSchemeX);
    my $taidValue  = $doc->find('/NCIPMessage/ItemShipped/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');

    my $address = $doc->findvalue('/NCIPMessage/ItemShipped/ShippingInformation/PhysicalAddress/UnstructuredAddress/UnstructuredAddressData');

    my $visid = $doc->findvalue('/NCIPMessage/ItemShipped/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier') . $faidValue;
    my $barcode = $doc->findvalue('/NCIPMessage/ItemShipped/UniqueItemId/ItemIdentifierValue') . $faidValue;
    my $title = $doc->findvalue('/NCIPMessage/ItemShipped/ItemOptionalFields/BibliographicDescription/Title');
    my $callnumber = $doc->findvalue('/NCIPMessage/ItemShipped/ItemOptionalFields/ItemDescription/CallNumber');

    my $copy = copy_from_barcode($barcode);

    fail( $copy->{textcode} . " $barcode" ) unless ( blessed $copy);

    my $pickup_lib;

    if ($address) {
        my $pickup_lib_map = load_map_file( $conf->{path}->{pickup_lib_map} );

        if ($pickup_lib_map) {
            $pickup_lib = lookup_pickup_lib($address, $pickup_lib_map);
        }
    }

    if ($pickup_lib) {
        update_hold_pickup($barcode, $pickup_lib);
    }

    my $r = update_copy_shipped( $copy, $conf->{status}->{transit}, $visid ); # put copy into INN-Reach Transit status & modify barcode = Visid != tempIIIiNumber
    if ($r ne 'SUCCESS') {
        fail( $r->{textcode} . ", Barcode: $barcode, Visible ID: $visid" )
    }

    my $hd = <<ITEMSHIPPED;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <ItemShippedResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$faidScheme</Scheme>
                    <Value>$faidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <UniqueItemId>
            <ItemIdentifierValue datatype="string">$visid</ItemIdentifierValue>
        </UniqueItemId>
    </ItemShippedResponse>
</NCIPMessage> 

ITEMSHIPPED

    logit( $hd, ( caller(0) )[3] );
    staff_log( $taidValue, $faidValue,
        "ItemShipped -> Visible Id : " . $visid . " | Barcode : " . $barcode . " | Title : " . $title . " | Call Number : " . $callnumber );
}

sub item_request {
    my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequested/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    my $faidScheme = HTML::Entities::encode($faidSchemeX);
    my $faidValue  = $doc->find('/NCIPMessage/ItemRequested/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');

    my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequested/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    my $taidScheme = HTML::Entities::encode($taidSchemeX);
    my $taidValue  = $doc->find('/NCIPMessage/ItemRequested/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');
    my $UniqueItemIdAgencyIdValue = $doc->findvalue('/NCIPMessage/ItemRequested/UniqueItemId/UniqueAgencyId/Value');

    # TODO: should we use the VisibleID for item agency variation of this method call

    my $pid = $doc->findvalue('/NCIPMessage/ItemRequested/UniqueUserId/UserIdentifierValue');
    my $barcode = $doc->findvalue('/NCIPMessage/ItemRequested/UniqueItemId/ItemIdentifierValue');
    my $author = $doc->findvalue('/NCIPMessage/ItemRequested/ItemOptionalFields/BibliographicDescription/Author');
    my $title = $doc->findvalue('/NCIPMessage/ItemRequested/ItemOptionalFields/BibliographicDescription/Title');
    my $callnumber = $doc->findvalue('/NCIPMessage/ItemRequested/ItemOptionalFields/ItemDescription/CallNumber');
    my $medium_type = $doc->find('/NCIPMessage/ItemRequested/ItemOptionalFields/BibliographicDescription/MediumType/Value');

    my $r = "default error checking response";

    if ( $barcode =~ /^i/ ) {    # XXX EG is User Agency # create copy only if barcode is an iNUMBER
        my $copy_status_id = $conf->{status}->{loan_requested}; # INN-Reach Loan Requested - local configured status
        $barcode .= $faidValue;
        # we want our custom status to be then end result, so create the copy with status of "Available, then hold it, then update the status
        $r = create_copy( $title, $callnumber, $barcode, 0, $medium_type );
        my $copy = copy_from_barcode($barcode);
        my $r2   = place_simple_hold( $copy->id, $pid );
        my $r3   = update_copy( $copy, $copy_status_id );
    } else {    # XXX EG is Item Agency
        unless ( $conf->{behavior}->{no_item_agency_holds} =~ m/^y/i ) {
            # place hold for user UniqueUserId/UniqueAgencyId/Value = institution account
            my $copy = copy_from_barcode($barcode);
            my $pid2 = 1013459; # XXX CUSTOMIZATION NEEDED XXX # this is the id of a user representing your DCB system, TODO: use agency information to create and link to individual accounts per agency, if needed
            $r = place_simple_hold( $copy->id, $pid2 );
            my $r2 = update_copy( $copy, $conf->{status}->{hold} ); # put into INN-Reach Hold status
        }
    }

    # Avoid generating invalid XML responses by encoding title/author/callnumber
    # TODO: Move away from heredocs for generating XML
	$title      = _naive_encode_xml($title);
	$author     = _naive_encode_xml($author);
	$callnumber = _naive_encode_xml($callnumber);

    my $hd = <<ITEMREQ;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <ItemRequestedResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$faidScheme</Scheme>
                    <Value>$faidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <UniqueUserId>
            <UniqueAgencyId>
                <Scheme datatype="string">$taidScheme</Scheme>
                <Value datatype="string">$taidValue</Value>
            </UniqueAgencyId>
            <UserIdentifierValue datatype="string">$pid</UserIdentifierValue>
        </UniqueUserId>
        <UniqueItemId>
            <ItemIdentifierValue datatype="string">$barcode</ItemIdentifierValue>
        </UniqueItemId>
        <ItemOptionalFields>
            <BibliographicDescription>
        <Author datatype="string">$author</Author>
        <Title datatype="string">$title</Title>
            </BibliographicDescription>
            <ItemDescription>
                <CallNumber datatype="string">$callnumber</CallNumber>
            </ItemDescription>
       </ItemOptionalFields>
    </ItemRequestedResponse>
</NCIPMessage> 

ITEMREQ

    logit( $hd, ( caller(0) )[3] );
    staff_log( $taidValue, $faidValue,
        "ItemRequested -> Barcode : " . $barcode . " | Title : " . $title . " | Call Number : " . $callnumber . " | Patronid :" . $pid );
}

sub lookupUser {

    my $faidScheme = $doc->findvalue('/NCIPMessage/LookupUser/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');
    $faidScheme = HTML::Entities::encode($faidScheme);
    my $faidValue = $doc->find('/NCIPMessage/LookupUser/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');
    my $taidScheme = $doc->findvalue('/NCIPMessage/LookupUser/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');
    $taidScheme = HTML::Entities::encode($taidScheme);

    my $taidValue = $doc->find('/NCIPMessage/LookupUser/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');
    my $id = $doc->findvalue('/NCIPMessage/LookupUser/VisibleUserId/VisibleUserIdentifier');

    my $uidValue;

    if ($patron_id_type eq 'barcode') {
        $uidValue = user_id_from_barcode($id);
    } else {
        $uidValue = $id;
    }

    if ( !defined($uidValue)
        || ( ref($uidValue) && reftype($uidValue) eq 'HASH' ) )
    {
        do_lookup_user_error_stanza("PATRON_NOT_FOUND : $id");
        die;
    }

    my ( $propername, $email, $good_until, $userpriv, $block_stanza ) =
      ( "name here", "", "good until", "", "" );    # defaults

    my $patron = flesh_user($uidValue);

    #if (blessed($patron)) {
    my $patron_ok = 1;
    my @penalties = @{ $patron->standing_penalties };

    if ( $patron->deleted eq 't' ) {
        do_lookup_user_error_stanza("PATRON_DELETED : $uidValue");
        die;
    } elsif ( $patron->barred eq 't' ) {
        do_lookup_user_error_stanza("PATRON_BARRED : $uidValue");
        die;
    } elsif ( $patron->active eq 'f' ) {
        do_lookup_user_error_stanza("PATRON_INACTIVE : $uidValue");
        die;
    }

    elsif ( $#penalties > -1 ) {

#                my $penalty;
#                   foreach $penalty (@penalties) {
#                    if (defined($penalty->standing_penalty->block_list)) {
#                            my @block_list = split(/\|/, $penalty->standing_penalty->block_list);
#                            foreach my $block (@block_list) {
#                                foreach my $block_on (@$block_types) {
#                                    if ($block eq $block_on) {
#                                        $block_stanza .= "\n".$penalty->standing_penalty->name;
#                                        $patron_ok = 0;
#                                    }
#                                    last unless ($patron_ok);
#                            }
#                                last unless ($patron_ok);
#                          }
#                     }
#                }
        $block_stanza = qq(
            <BlockOrTrap>
                <UniqueAgencyId>
                    <Scheme datatype="string">http://just.testing.now</Scheme>
                    <Value datatype="string">$faidValue</Value>
                </UniqueAgencyId>
                <BlockOrTrapType>
                    <Scheme datatype="string">http://just.testing.now</Scheme>
                    <Value datatype="string">Block Hold</Value>
                </BlockOrTrapType>
            </BlockOrTrap>);
    }

    if ( defined( $patron->email ) && $conf->{behavior}->{omit_patron_email} !~ m/^y/i ) {
        $email = qq(
            <UserAddressInformation>
                <ElectronicAddress>
                    <ElectronicAddressType>
                        <Scheme datatype="string">http://testing.now</Scheme>
                        <Value datatype="string">mailto</Value>
                    </ElectronicAddressType>
                    <ElectronicAddressData datatype="string">)
          . HTML::Entities::encode( $patron->email )
          . qq(</ElectronicAddressData>
                </ElectronicAddress>
            </UserAddressInformation>);
    }

    #$propername = $patron->first_given_name . " " . $patron->family_name;
    $propername = $patron->family_name . ", " . $patron->first_given_name . " " .$patron->second_given_name;
    $good_until = $patron->expire_date || "unknown";
    $userpriv = $patron->profile->name;

    my $userpriv_map = load_map_file( $conf->{path}->{userpriv_map} );

    if ($userpriv_map) {
        $userpriv = lookup_userpriv($userpriv, $userpriv_map);
    }

    #} else {
    #    do_lookup_user_error_stanza("PATRON_NOT_FOUND : $id");
    #    die;
    #}
    my $uniqid = $patron->id;
    my $visid;
    if ($patron_id_type eq 'barcode') {
        $visid = $patron->card->barcode;
    } else {
        $visid = $patron->id;
    }
    my $hd = <<LOOKUPUSERRESPONSE;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <LookupUserResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>$taidScheme</Scheme>
                    <Value>$taidValue</Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                   <Scheme>$faidScheme</Scheme>
                   <Value>$faidValue</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <UniqueUserId>
            <UniqueAgencyId>
                <Scheme>$taidScheme</Scheme>
                <Value>$taidValue</Value>
            </UniqueAgencyId>
            <UserIdentifierValue>$uniqid</UserIdentifierValue>
        </UniqueUserId>
        <UserOptionalFields>
            <VisibleUserId>
                <VisibleUserIdentifierType>
                    <Scheme datatype="string">http://blah.com</Scheme>
                    <Value datatype="string">Barcode</Value>
                </VisibleUserIdentifierType>
                <VisibleUserIdentifier datatype="string">$visid</VisibleUserIdentifier>
            </VisibleUserId>
            <NameInformation>
                <PersonalNameInformation>
                    <UnstructuredPersonalUserName datatype="string">$propername</UnstructuredPersonalUserName>
                </PersonalNameInformation>
            </NameInformation>
            <UserPrivilege>
                <UniqueAgencyId>
                    <Scheme datatype="string">$faidScheme</Scheme>
                    <Value datatype="string">$faidValue</Value>
                </UniqueAgencyId>
                <AgencyUserPrivilegeType>
                    <Scheme datatype="string">http://testing.purposes.only</Scheme>
                    <Value datatype="string">$userpriv</Value>
                </AgencyUserPrivilegeType>
                <ValidToDate datatype="string">$good_until</ValidToDate>
            </UserPrivilege> $email $block_stanza
        </UserOptionalFields>
   </LookupUserResponse>
</NCIPMessage>

LOOKUPUSERRESPONSE

    logit( $hd, ( caller(0) )[3] );
    staff_log( $taidValue, $faidValue,
            "LookupUser -> Patron Barcode : "
          . $id
          . " | Patron Id : "
          . $uidValue
          . " | User Name : "
          . $propername
          . " | User Priv : "
          . $userpriv );
}

sub fail {
    my $error_msg =
      shift || "THIS IS THE DEFAULT / DO NOT HANG III NCIP RESP MSG";
    print "Content-type: text/xml\n\n";

    print <<ITEMREQ;
<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <ItemRequestedResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>http://72.52.134.169:6601/IRCIRCD?target=get_scheme_values&amp;scheme=UniqueAgencyId</Scheme>
                    <Value></Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>http://72.52.134.169:6601/IRCIRCD?target=get_scheme_values&amp;scheme=UniqueAgencyId</Scheme>
                    <Value>$error_msg</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
    </ItemRequestedResponse>
</NCIPMessage>

ITEMREQ

    # XXX: we should log FromAgencyId and ToAgencyId values here, but they are not available to the code at this point
    staff_log( '', '',
        ( ( caller(0) )[3] . " -> " . $error_msg ) );
    die;
}

sub do_lookup_user_error_stanza {

    # XXX: we should include FromAgencyId and ToAgencyId values, but they are not available to the code at this point
    my $error = shift;
    my $hd    = <<LOOKUPPROB;
Content-type: text/xml


<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <LookupUserResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme></Scheme>
                    <Value></Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme></Scheme>
                    <Value></Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
        <Problem>
            <ProcessingError>
                <ProcessingErrorType>
                    <Scheme>http://www.niso.org/ncip/v1_0/schemes/processingerrortype/lookupuserprocessingerror.scm</Scheme>
                    <Value>$error</Value>
                </ProcessingErrorType>
                <ProcessingErrorElement>
                    <ElementName>AuthenticationInput</ElementName>
                </ProcessingErrorElement>
            </ProcessingError>
        </Problem>
    </LookupUserResponse>
</NCIPMessage>

LOOKUPPROB

    logit( $hd, ( caller(0) )[3] );
    # XXX: we should log FromAgencyId and ToAgencyId values here, but they are not available to the code at this point
    staff_log( '', '', ( ( caller(0) )[3] . " -> " . $error ) );
    die;
}

# Login to the OpenSRF system/Evergreen.
#
# Returns a hash with the authtoken, authtime, and expiration (time in
# seconds since 1/1/1970).
sub login {

 # XXX: local opensrf core conf filename should be in config.
 # XXX: STAFF account with ncip service related permissions should be in config.
    my $bootstrap = '/openils/conf/opensrf_core.xml';
    my $uname     = $conf->{auth}->{username};
    my $password  = $conf->{auth}->{password};

    # Bootstrap the client
    OpenSRF::System->bootstrap_client( config_file => $bootstrap );
    my $idl = OpenSRF::Utils::SettingsClient->new->config_value("IDL");
    Fieldmapper->import( IDL => $idl );

    # Initialize CStoreEditor:
    OpenILS::Utils::CStoreEditor->init;

    my $seed = OpenSRF::AppSession->create('open-ils.auth')
      ->request( 'open-ils.auth.authenticate.init', $uname )->gather(1);

    return undef unless $seed;

    my $response = OpenSRF::AppSession->create('open-ils.auth')->request(
        'open-ils.auth.authenticate.complete',
        {
            username => $uname,
            password => md5_hex( $seed . md5_hex($password) ),
            type     => 'staff'
        }
    )->gather(1);

    return undef unless $response;

    my %result;
    $result{'authtoken'}  = $response->{payload}->{authtoken};
    $result{'authtime'}   = $response->{payload}->{authtime};
    $result{'expiration'} = time() + $result{'authtime'}
      if ( defined( $result{'authtime'} ) );
    return %result;
}

# Check the time versus the session expiration time and login again if
# the session has expired, consequently resetting the session
# paramters. We want to run this before doing anything that requires
# us to have a current session in OpenSRF.
#
# Arguments
# none
#
# Returns
# Nothing
sub check_session_time {
    if ( time() > $session{'expiration'} ) {
        %session = login();
        if ( !%session ) {
            die("Failed to reinitialize the session after expiration.");
        }
    }
}

# Retrieve the logged in user.
#
sub get_session {
    my $response =
      OpenSRF::AppSession->create('open-ils.auth')
      ->request( 'open-ils.auth.session.retrieve', $session{authtoken} )
      ->gather(1);
    return $response;
}

# Logout/destroy the OpenSRF session
#
# Argument is
# none
#
# Returns
# Does not return anything
sub logout {
    if ( time() < $session{'expiration'} ) {
        my $response =
          OpenSRF::AppSession->create('open-ils.auth')
          ->request( 'open-ils.auth.session.delete', $session{authtoken} )
          ->gather(1);
        if ($response) {

            # strong.silent.success
            exit(0);
        } else {
            fail("Logout unsuccessful. Good-bye, anyway.");
        }
    }
}

sub update_copy {
    check_session_time();
    my ( $copy, $status_id ) = @_;
    my $e = new_editor( authtoken => $session{authtoken} );
    return $e->event->{textcode} unless ( $e->checkauth );
    $e->xact_begin;
    $copy->status($status_id);
    return $e->event unless $e->update_asset_copy($copy);
    $e->commit;
    return 'SUCCESS';
}

# my paranoia re barcode on shipped items using visid for unique value
sub update_copy_shipped {
    check_session_time();
    my ( $copy, $status_id, $barcode ) = @_;
    my $e = new_editor( authtoken => $session{authtoken} );
    return $e->event unless ( $e->checkauth );
    $e->xact_begin;
    $copy->status($status_id);
    $copy->barcode($barcode);
    return $e->event unless $e->update_asset_copy($copy);
    $e->commit;
    return 'SUCCESS';
}

# Delete a copy
#
# Argument
# Fieldmapper asset.copy object
#
# Returns
# "SUCCESS" on success
# Event textcode if an error occurs
sub delete_copy {
    check_session_time();
    my ($copy) = @_;

    my $e = new_editor( authtoken => $session{authtoken} );
    return $e->event->{textcode} unless ( $e->checkauth );

    # Get the calnumber
    my $vol = $e->retrieve_asset_call_number( $copy->call_number );
    return $e->event->{textcode} unless ($vol);

    # Get the biblio.record_entry
    my $bre = $e->retrieve_biblio_record_entry( $vol->record );
    return $e->event->{textcode} unless ($bre);

    # Delete everything in a transaction and rollback if anything fails.
    # TODO: I think there is a utility function which handles all this
    $e->xact_begin;
    my $r;    # To hold results of editor calls
    $r = $e->delete_asset_copy($copy);
    unless ($r) {
        my $lval = $e->event->{textcode};
        $e->rollback;
        return $lval;
    }
    my $list =
      $e->search_asset_copy( { call_number => $vol->id, deleted => 'f' } );
    unless (@$list) {
        $r = $e->delete_asset_call_number($vol);
        unless ($r) {
            my $lval = $e->event->{textcode};
            $e->rollback;
            return $lval;
        }
        $list = $e->search_asset_call_number( { record => $bre->id, deleted => 'f' } );
        unless (@$list) {
            $r = $e->delete_biblio_record_entry($bre);
            unless ($r) {
                my $lval = $e->event->{textcode};
                $e->rollback;
                return $lval;
            }
        }
    }
    $e->commit;
    return 'SUCCESS';
}

# Get asset.copy from asset.copy.barcode.
# Arguments
# copy barcode
#
# Returns
# asset.copy fieldmaper object
# or hash on error
sub copy_from_barcode {
    check_session_time();
    my ($barcode) = @_;
    my $response =
      OpenSRF::AppSession->create('open-ils.search')
      ->request( 'open-ils.search.asset.copy.find_by_barcode', $barcode )
      ->gather(1);
    return $response;
}

sub locid_from_barcode {
    my ($barcode) = @_;
    my $response =
      OpenSRF::AppSession->create('open-ils.search')
      ->request( 'open-ils.search.biblio.find_by_barcode', $barcode )
      ->gather(1);
    return $response->{ids}[0];
}

sub bre_id_from_barcode {
    check_session_time();
    my ($barcode) = @_;
    my $response =
      OpenSRF::AppSession->create('open-ils.search')
      ->request( 'open-ils.search.bib_id.by_barcode', $barcode )
      ->gather(1);
    return $response;
}

sub holds_for_bre {
    check_session_time();
    my ($bre_id) = @_;
    my $response =
      OpenSRF::AppSession->create('open-ils.circ')
      ->request( 'open-ils.circ.holds.retrieve_all_from_title', $session{authtoken}, $bre_id )
      ->gather(1);
    return $response;

}

# Convert a MARC::Record to XML for Evergreen
#
# Copied from Dyrcona's issa framework which copied
# it from MVLC's Safari Load program which copied it
# from some code in the Open-ILS example import scripts.
#
# Argument
# A MARC::Record object
#
# Returns
# String with XML for the MARC::Record as Evergreen likes it
sub convert2marcxml {
    my $input = shift;
    ( my $xml = $input->as_xml_record() ) =~ s/\n//sog;
    $xml =~ s/^<\?xml.+\?\s*>//go;
    $xml =~ s/>\s+</></go;
    $xml =~ s/\p{Cc}//go;
    $xml = $U->entityize($xml);
    $xml =~ s/[\x00-\x1f]//go;
    return $xml;
}

# Create a copy and marc record
#
# Arguments
# title
# call number
# copy barcode
#
# Returns
# bib id on succes
# event textcode on failure
sub create_copy {
    check_session_time();
    my ( $title, $callnumber, $barcode, $copy_status_id, $medium_type ) = @_;

    my $e = new_editor( authtoken => $session{authtoken} );
    return $e->event->{textcode} unless ( $e->checkauth );

    my $r = $e->allowed( [ 'CREATE_COPY', 'CREATE_MARC', 'CREATE_VOLUME' ] );
    if ( ref($r) eq 'HASH' ) {
        return $r->{textcode} . ' ' . $r->{ilsperm};
    }

    # Check if the barcode exists in asset.copy and bail if it does.
    my $list = $e->search_asset_copy( { deleted => 'f', barcode => $barcode } );
    if (@$list) {
# in the future, can we update it, if it exists and only if it is an INN-Reach status item ?
        $e->finish;
        fail( 'BARCODE_EXISTS ! Barcode : ' . $barcode );
        die;
    }

    # Create MARC record
    my $record = MARC::Record->new();
    $record->encoding('UTF-8');
    $record->leader('00881nam a2200193 4500');
    my $datespec = strftime( "%Y%m%d%H%M%S.0", localtime );
    my @fields = ();
    push( @fields, MARC::Field->new( '005', $datespec ) );
    push( @fields, MARC::Field->new( '082', '0', '4', 'a' => $callnumber ) );
    push( @fields, MARC::Field->new( '245', '0', '0', 'a' => $title ) );
    $record->append_fields(@fields);

    # Convert the record to XML
    my $xml = convert2marcxml($record);

    my $bre =
      OpenSRF::AppSession->create('open-ils.cat')
      ->request( 'open-ils.cat.biblio.record.xml.import',
        $session{authtoken}, $xml, 'System Local', 1 )->gather(1);
    return $bre->{textcode} if ( ref($bre) eq 'HASH' );

    # Create volume record
    my $vol =
      OpenSRF::AppSession->create('open-ils.cat')
      ->request( 'open-ils.cat.call_number.find_or_create', $session{authtoken}, $callnumber, $bre->id, $conf->{volume}->{owning_lib} )
      ->gather(1);
    return $vol->{textcode} if ( $vol->{textcode} );

    # Retrieve the user
    my $user = get_session;

    # Create copy record
    my $copy = Fieldmapper::asset::copy->new();
    # XXX CUSTOMIZATION NEEDED XXX
    # You will need to either create a circ mod for every expected medium type,
    # OR you should create a single circ mod for all requests from the external
    # system.
    # Adjust these lines as needed.
    #    $copy->circ_modifier(qq($medium_type)); # XXX CUSTOMIZATION NEEDED XXX
    # OR
    $copy->circ_modifier($conf->{copy}->{circ_modifier});
    $copy->barcode($barcode);
    $copy->call_number( $vol->{acn_id} );
    $copy->circ_lib($conf->{copy}->{circ_lib});
    $copy->circulate('t');
    $copy->holdable('t');
    $copy->opac_visible('t');
    $copy->deleted('f');
    $copy->fine_level(2);
    $copy->loan_duration(2);
    $copy->location($conf->{copy}->{location});
    $copy->status($copy_status_id);
    $copy->editor('1');
    $copy->creator('1');

    $e->xact_begin;
    $copy = $e->create_asset_copy($copy);

    $e->commit;
    return $e->event->{textcode} unless ($r);
    return 'SUCCESS';
}

# Checkout a copy to a patron
#
# Arguments
# copy barcode
# patron barcode
#
# Returns
# textcode of the OSRF response.
sub checkout {
    check_session_time();
    my ( $copy_barcode, $patron_barcode, $due_date ) = @_;

    # Check for copy:
    my $copy = copy_from_barcode($copy_barcode);
    unless ( defined($copy) && blessed($copy) ) {
        return 'COPY_BARCODE_NOT_FOUND : ' . $copy_barcode;
    }

    # Check for user
    my $uid;
    if ($patron_id_type eq 'barcode') {
        $uid = user_id_from_barcode($patron_barcode);
    } else {
        $uid = $patron_barcode;
    }
    return 'PATRON_BARCODE_NOT_FOUND : ' . $patron_barcode if ( ref($uid) );

    my $response = OpenSRF::AppSession->create('open-ils.circ')->request(
        'open-ils.circ.checkout.full.override',
        $session{authtoken},
        {
            copy_barcode => $copy_barcode,
            patron_id    => $uid,
            due_date     => $due_date
        }
    )->gather(1);
    return $response->{textcode};
}

sub renewal {
    check_session_time();
    my ( $copy_barcode, $due_date ) = @_;

    # Check for copy:
    my $copy = copy_from_barcode($copy_barcode);
    unless ( defined($copy) && blessed($copy) ) {
        return 'COPY_BARCODE_NOT_FOUND : ' . $copy_barcode;
    }

    my $response = OpenSRF::AppSession->create('open-ils.circ')->request(
        'open-ils.circ.renew.override',
        $session{authtoken},
        {
            copy_barcode => $copy_barcode,
            due_date     => $due_date
        }
    )->gather(1);
    return $response->{textcode};
}

# Check a copy in
#
# Arguments
# copy barcode
#
# Returns
# "SUCCESS" on success
# textcode of a failed OSRF request
# 'COPY_NOT_CHECKED_OUT' when the copy is not checked out

sub checkin {
    check_session_time();
    my ($barcode, $noop) = @_;
    $noop ||= 0;

    my $copy = copy_from_barcode($barcode);
    return $copy->{textcode} unless ( blessed $copy);

    return ("COPY_NOT_CHECKED_OUT $barcode")
      unless ( $copy->status == OILS_COPY_STATUS_CHECKED_OUT );

    my $e = new_editor( authtoken => $session{authtoken} );
    return $e->event->{textcode} unless ( $e->checkauth );

    my $circ = $e->search_action_circulation(
        [ { target_copy => $copy->id, xact_finish => undef } ] )->[0];
    my $r =
      OpenSRF::AppSession->create('open-ils.circ')
      ->request( 'open-ils.circ.checkin.override',
        $session{authtoken}, { force => 1, copy_id => $copy->id, noop => $noop } )->gather(1);
    return 'SUCCESS' if ( $r->{textcode} eq 'ROUTE_ITEM' );
    return $r->{textcode};
}

# Check in an copy as part of accept_item
# Intent is for the copy to be captured for
# a hold -- the only hold that should be
# present on the copy

sub checkin_accept {
    check_session_time();
    my $copy_id = shift;
    my $circ_lib = shift;

    my $r = OpenSRF::AppSession->create('open-ils.circ')->request(
        'open-ils.circ.checkin.override',
        $session{authtoken}, { force => 1, copy_id => $copy_id, circ_lib => $circ_lib }
    )->gather(1);

    return $r->{textcode};
}

# Get actor.usr.id from barcode.
# Arguments
# patron barcode
#
# Returns
# actor.usr.id
# or hash on error
sub user_id_from_barcode {
    check_session_time();
    my ($barcode) = @_;

    my $response;

    my $e = new_editor( authtoken => $session{authtoken} );
    return $response unless ( $e->checkauth );

    my $card = $e->search_actor_card( { barcode => $barcode, active => 't' } );
    return $e->event unless ($card);

    $response = $card->[0]->usr if (@$card);

    $e->finish;

    return $response;
}

# Place a simple hold for a patron.
#
# Arguments
# Target object appropriate for type of hold
# Patron for whom the hold is place
#
# Returns
# "SUCCESS" on success
# textcode of a failed OSRF request
# "HOLD_TYPE_NOT_SUPPORTED" if the hold type is not supported
# (Currently only support 'T' and 'C')

# simple hold should be removed and full holds sub should be used instead - pragmatic solution only

sub place_simple_hold {
    check_session_time();

    #my ($type, $target, $patron, $pickup_ou) = @_;
    my ( $target, $patron_id ) = @_;

    require $conf->{path}->{oils_header};
    use vars qw/ $apputils $memcache $user $authtoken $authtime /;

    osrf_connect( $conf->{path}->{opensrf_core} );
    oils_login( $conf->{auth}->{username}, $conf->{auth}->{password} );
    my $ahr = Fieldmapper::action::hold_request->new();
    $ahr->hold_type('C');
    # The targeter doesn't like our special statuses, and changing the status after the targeter finishes is difficult because it runs asynchronously.  Our workaround is to create the hold frozen, unfreeze it, then run the targeter manually.
    $ahr->target($target);
    $ahr->usr($patron_id);
    $ahr->requestor($conf->{hold}->{requestor});
    # NOTE: When User Agency, we don't know the pickup location until ItemShipped time
    # TODO: When Item Agency and using holds, set this to requested copy's circ lib?
    $ahr->pickup_lib($conf->{hold}->{init_pickup_lib});
    $ahr->phone_notify(''); # TODO: set this based on usr prefs
    $ahr->email_notify(1); # TODO: set this based on usr prefs
    $ahr->frozen('t');
    my $resp = simplereq( CIRC(), 'open-ils.circ.holds.create', $authtoken, $ahr );
    my $e = new_editor( xact => 1, authtoken => $session{authtoken} );
    $ahr = $e->retrieve_action_hold_request($resp);    # refresh from db
    if (!ref $ahr) {
        $e->rollback;
        fail("place_simple_hold: hold request not placed!");
    }
    $ahr->frozen('f');
    $e->update_action_hold_request($ahr);
    $e->commit;
    $U->storagereq( 'open-ils.storage.action.hold_request.copy_targeter', undef, $ahr->id );

    #oils_event_die($resp);
    my $errors = "";
    if ( ref($resp) eq 'ARRAY' ) {
        ( $errors .= "error : " . $_->{textcode} ) for @$resp;
        return $errors;
    } elsif ( ref($resp) ne 'HASH' ) {
        return "Hold placed! hold_id = " . $resp . "\n";
    }
}

sub find_hold_on_copy {
    check_session_time();

    my ( $copy_barcode ) = @_;

    # start with barcode of item, find bib ID
    my $rec = bre_id_from_barcode($copy_barcode);

    return undef unless $rec;

    # call for holds on that bib
    my $holds = holds_for_bre($rec);

    # There should only be a single copy hold
    my $hold_id = @{$holds->{copy_holds}}[0];

    return undef unless $hold_id;

    my $hold_details =
      OpenSRF::AppSession->create('open-ils.circ')
      ->request( 'open-ils.circ.hold.details.retrieve', $session{authtoken}, $hold_id )
      ->gather(1);

    my $hold = $hold_details->{hold};

    return undef unless blessed($hold);

    return $hold;
}

sub update_hold_pickup {
    check_session_time();

    my ( $copy_barcode, $pickup_lib ) = @_;

    my $hold = find_hold_on_copy($copy_barcode);

    # return if hold was not found
    return undef unless defined($hold) && blessed($hold);

    $hold->pickup_lib($pickup_lib);

    # update the copy hold with the new pickup lib information
    my $result =
      OpenSRF::AppSession->create('open-ils.circ')
      ->request( 'open-ils.circ.hold.update', $session{authtoken}, $hold )
      ->gather(1);

    return $result;
}

sub cancel_hold {
    check_session_time();

    my ( $copy_barcode ) = @_;

    my $hold = find_hold_on_copy($copy_barcode);

    # return if hold was not found
    return undef unless defined($hold) && blessed($hold);

    $hold->cancel_time('now()');
    $hold->cancel_cause(5); # 5 = 'Staff forced' (perhaps it should be 'Patron via SIP'?) or OPAC? or add NCIP to the cause table?
    $hold->cancel_note('NCIP cancellation request');

    # update the copy hold with the new pickup lib information
    my $result =
      OpenSRF::AppSession->create('open-ils.circ')
      ->request( 'open-ils.circ.hold.update', $session{authtoken}, $hold )
      ->gather(1);

    return $result;
}

# Flesh user information
# Arguments
# actor.usr.id
#
# Returns
# fieldmapped, fleshed user or
# event hash on error
sub flesh_user {
    check_session_time();
    my ($id) = @_;
    my $response =
      OpenSRF::AppSession->create('open-ils.actor')
      ->request( 'open-ils.actor.user.fleshed.retrieve',
        $session{'authtoken'}, $id,
        [ 'card', 'cards', 'standing_penalties', 'home_ou', 'profile' ] )
      ->gather(1);
    return $response;
}

sub _naive_encode_xml {
    my $val = shift;

    $val =~ s/&/&amp;/g;
    $val =~ s/</&lt;/g;
    $val =~ s/>/&gt;/g;

    return $val;
}
