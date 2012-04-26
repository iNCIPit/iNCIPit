#! /usr/bin/perl 

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
use XML::LibXML;
use CGI::XMLPost;
use HTML::Entities;
use CGI::Carp;
use XML::XPath;
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

my $xmlpost = CGI::XMLPost->new();
my $xml = $xmlpost->data(); 

# log posted data 
open POST_DATA, ">>post_data.txt";
print POST_DATA $xml;
close POST_DATA;

# initialize the parser
my $parser = new XML::LibXML;
my $doc = $parser->load_xml( string => $xml );

my %session = login();

# Setup our SIGALRM handler.
$SIG{'ALRM'} = \&logout;

if (defined($session{authtoken})) {
    $doc->exists('/NCIPMessage/LookupUser') ? lookupUser() :
        ( $doc->exists('/NCIPMessage/ItemRequested') ? item_request() :
                ( $doc->exists('/NCIPMessage/ItemShipped') ? item_shipped() :
                        ( $doc->exists('/NCIPMessage/ItemCheckedOut') ? item_checked_out() :
                          ( $doc->exists('/NCIPMessage/CheckOutItem') ? check_out_item() :
                                ( $doc->exists('/NCIPMessage/ItemCheckedIn') ? item_checked_in() :
                                  ( $doc->exists('/NCIPMessage/CheckInItem') ? check_in_item() :
                                        ( $doc->exists('/NCIPMessage/ItemReceived') ? item_received() :
                                                ( $doc->exists('/NCIPMessage/AcceptItem') ? accept_item() :
                                                	( $doc->exists('/NCIPMessage/ItemRequestCancelled') ? item_cancelled() :
                                                		( $doc->exists('/NCIPMessage/ItemRenewed') ? item_renew() :
                                                        		fail("UNKNOWN NCIPMessage")
								)
							)
                                                )
                                        )
				  )
                                )
                          )
			)
                )
        );

    # Clear any SIGALRM timers.
    alarm(0);
    logout();
} else {
    # red dwarf - s1:e1
    fail("They are all dead, Dave.");
}

sub logit {
	my ($msg,$func) = @_;
	open RESP_DATA, ">>resp_data.txt";
	print RESP_DATA $msg;
	close RESP_DATA;
	print $msg || fail($func);
}

sub item_renew {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemRenewed/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/ItemRenewed/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemRenewed/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/ItemRenewed/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

	my $pid         = $doc->findvalue('/NCIPMessage/ItemRenewed/UniqueUserId/UserIdentifierValue');  
	my $visid      = $doc->findvalue('/NCIPMessage/ItemRenewed/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier').$faidValue;  
	#my $barcode    = $doc->findvalue('/NCIPMessage/ItemRenewed/UniqueItemId/ItemIdentifierValue').$faidValue;  
	my $due_date   = $doc->findvalue('/NCIPMessage/ItemRenewed/DateDue');  

	#my $copy = copy_from_barcode($barcode);
	#fail($copy->{textcode}) unless (blessed $copy);
	#my $r = update_copy($copy,0); # seemed like copy had to be available before it could be checked out, so ...
	#my $r2 = checkout($barcode,$pid,$due_date);
	my $r = renewal($visid,$due_date);

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

VISID 			= $visid
Desired Due Date 	= $date_due

MOREINFO

	$hd .= $more_info;

	logit($hd,(caller(0))[3]);
}

sub accept_item {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/AcceptItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/AcceptItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/AcceptItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/AcceptItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

	my $visid      = $doc->findvalue('/NCIPMessage/AcceptItem/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier').$faidValue;  
	my $request_id = $doc->findvalue('/NCIPMessage/AcceptItem/UniqueRequestId/RequestIdentifierValue') || "unknown" ;  
#	my $barcode    = $doc->findvalue('/NCIPMessage/AcceptItem/UniqueItemId/ItemIdentifierValue').$faidValue;  
	my $patron     = $doc->findvalue('/NCIPMessage/AcceptItem/UserOptionalFields/VisibleUserId/VisibleUserIdentifier');  
#	my $copy = copy_from_barcode($barcode);
#     my $r = place_hold ('C', $copy, $patron, OUHERE);
	my $copy = copy_from_barcode($visid);
	my $r2 = update_copy($copy,112); # put into INN-Reach Hold status

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

	logit($hd,(caller(0))[3]);
}

sub item_received {
     my $faidValue  = $doc->find('/NCIPMessage/ItemReceived/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
     my $barcode      = $doc->findvalue('/NCIPMessage/ItemReceived/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier').$faidValue;  
     #my $barcode = $doc->findvalue('/NCIPMessage/ItemReceived/UniqueItemId/ItemIdentifierValue').$faidValue;  
     my $copy = copy_from_barcode($barcode);
     fail($copy->{textcode}) unless (blessed $copy);
     my $r1 = checkin($barcode,OUHERE) if ($copy->status == OILS_COPY_STATUS_CHECKED_OUT); # checkin the item before delete if ItemCheckedIn step was skipped
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
            <ItemIdentifierValue datatype="string">$barcode</ItemIdentifierValue>
        </UniqueItemId>
    </ItemReceivedResponse>
</NCIPMessage> 

ITEMRECEIVED

	logit($hd,(caller(0))[3]);
}

sub item_cancelled {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/ItemRequestCancelled/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  

	my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/ItemRequestCancelled/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  
	my $UniqueItemIdAgencyIdValue  = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/UniqueItemId/UniqueAgencyId/Value');  

     	#my $barcode      = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier').$faidValue;  
	my $barcode = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/UniqueItemId/ItemIdentifierValue').$faidValue;  

	if ($UniqueItemIdAgencyIdValue eq SPECIALTOAGENCY ) { 
	#        my $localid = locid_from_barcode($barcode);
	#	$r = place_hold($localid, SPECIALTOAGEID );
	# remove hold!
	} 
	else {
     		my $copy = copy_from_barcode($barcode);
     		fail($copy->{textcode}) unless (blessed $copy);
     		my $r = delete_copy($copy);
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

	logit($hd,(caller(0))[3]);
}

sub item_checked_in {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedIn/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/ItemCheckedIn/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedIn/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/ItemCheckedIn/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

     	my $barcode      = $doc->findvalue('/NCIPMessage/ItemCheckedIn/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier').$faidValue;  
     	# my $barcode    = $doc->findvalue('/NCIPMessage/ItemCheckedIn/UniqueItemId/ItemIdentifierValue').$faidValue;  
     	my $r = checkin($barcode, PICKUPLOCATION );  
     	my $copy = copy_from_barcode($barcode);
     	my $r2 = update_copy($copy,114); # "INN-Reach Transit Return" status

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
            <ItemIdentifierValue datatype="string">$barcode</ItemIdentifierValue>
        </UniqueItemId>
    </ItemCheckedInResponse>
</NCIPMessage> 

ITEMCHECKEDIN

	logit($hd,(caller(0))[3]);
}

sub item_checked_out {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedOut/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/ItemCheckedOut/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedOut/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/ItemCheckedOut/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

	my $pid         = $doc->findvalue('/NCIPMessage/ItemCheckedOut/UserOptionalFields/VisibleUserId/VisibleUserIdentifier');  
	# my $barcode    = $doc->findvalue('/NCIPMessage/ItemCheckedOut/UniqueItemId/ItemIdentifierValue').$faidValue;  
	my $due_date   = $doc->findvalue('/NCIPMessage/ItemCheckedOut/DateDue');  
	# my $title    = $doc->findvalue('/NCIPMessage/ItemCheckedOut/ItemOptionalFields/BibliographicDescription/Title');  
	
	my $visid    = $doc->findvalue('/NCIPMessage/ItemCheckedOut/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier').$faidValue;  

	my $copy = copy_from_barcode($visid);
	fail($copy->{textcode}) unless (blessed $copy);
	my $r = update_copy($copy,0); # seemed like copy had to be available before it could be checked out, so ...
     	# my $r1 = checkin($visid, PICKUPOU ) if ($copy->status == OILS_COPY_STATUS_CHECKED_OUT); # double posted itemcheckedout messages cause error ... trying to simplify 
	my $r2 = checkout($visid,$pid,$due_date);

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

$hd .= $r;
	logit($hd,(caller(0))[3]);
}

sub check_out_item {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/CheckOutItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/CheckOutItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/CheckOutItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/CheckOutItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

	my $mdate       = $doc->findvalue('/NCIPMessage/CheckOutItem/MandatedAction/DateEventOccurred');  
	my $pid         = $doc->find('/NCIPMessage/CheckOutItem/UserOptionalFields/UniqueAgencyId/Value');  

	my $barcode    = $doc->findvalue('/NCIPMessage/CheckOutItem/UniqueItemId/ItemIdentifierValue');  
	my $due_date   = $doc->findvalue('/NCIPMessage/CheckOutItem/DateDue');  

	my $copy = copy_from_barcode($barcode);
	fail($copy->{textcode}) unless (blessed $copy);
	# my $r = update_copy($copy,0); # seemed like copy had to be available before it could be checked out, so ...

	my $r2 = checkout($barcode,$pid,$due_date);

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

	logit($hd,(caller(0))[3]);
}

sub check_in_item {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/CheckInItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/CheckInItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/CheckInItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/CheckInItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

     	my $barcode    = $doc->findvalue('/NCIPMessage/CheckInItem/UniqueItemId/ItemIdentifierValue');  
     	my $r = checkin($barcode, OUHERE);  
     	my $copy = copy_from_barcode($barcode);
     	my $r2 = update_copy($copy,0); # Available now 

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

	logit($hd,(caller(0))[3]);
}

sub item_shipped {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemShipped/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/ItemShipped/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemShipped/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/ItemShipped/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

	my $visid      = $doc->findvalue('/NCIPMessage/ItemShipped/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier').$faidValue;  
	my $barcode    = $doc->findvalue('/NCIPMessage/ItemShipped/UniqueItemId/ItemIdentifierValue').$faidValue;  
	my $title    = $doc->findvalue('/NCIPMessage/ItemShipped/ItemOptionalFields/BibliographicDescription/Title');  
	my $callnumber    = $doc->findvalue('/NCIPMessage/ItemShipped/ItemOptionalFields/ItemDescription/CallNumber');  

	my $copy = copy_from_barcode($barcode);
	fail($copy->{textcode}) unless (blessed $copy);
	my $r = update_copy_shipped($copy,113,$visid); # put copy into INN-Reach Transit status & modify barcode = Visid != tempIIIiNumber

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

	logit($hd,(caller(0))[3]);
}

sub item_request {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequested/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/ItemRequested/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  

	my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequested/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/ItemRequested/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  
	my $UniqueItemIdAgencyIdValue  = $doc->findvalue('/NCIPMessage/ItemRequested/UniqueItemId/UniqueAgencyId/Value');  

	my $id         = $doc->findvalue('/NCIPMessage/ItemRequested/UniqueUserId/UserIdentifierValue');  
	my $barcode    = $doc->findvalue('/NCIPMessage/ItemRequested/UniqueItemId/ItemIdentifierValue'); 
	my $author    = $doc->findvalue('/NCIPMessage/ItemRequested/ItemOptionalFields/BibliographicDescription/Author');  
	my $title    = $doc->findvalue('/NCIPMessage/ItemRequested/ItemOptionalFields/BibliographicDescription/Title');  
	my $callnumber    = $doc->findvalue('/NCIPMessage/ItemRequested/ItemOptionalFields/ItemDescription/CallNumber');  

	my $r = "default error checking response"; 

	if ($UniqueItemIdAgencyIdValue eq SPECIALFROMAGENCY ) { 
        	my $localid = locid_from_barcode($barcode);
		$r = place_simple_hold($localid, SPECIALFROMAGENCYID );
	} 
	else {
		my $copy_status_id = 110; # INN-Reach loan 
		$barcode .= $faidValue;
		$r = create_copy($title, $callnumber, $barcode, $copy_status_id);
	}

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
            <UserIdentifierValue datatype="string">$id</UserIdentifierValue>
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

	logit($hd,(caller(0))[3]);
}


sub lookupUser { 

my $faidScheme = $doc->findvalue('/NCIPMessage/LookupUser/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
$faidScheme = HTML::Entities::encode($faidScheme);
my $faidValue  = $doc->find('/NCIPMessage/LookupUser/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
my $taidScheme = $doc->findvalue('/NCIPMessage/LookupUser/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
$taidScheme = HTML::Entities::encode($taidScheme);

my $taidValue  = $doc->find('/NCIPMessage/LookupUser/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  
my $id         = $doc->findvalue('/NCIPMessage/LookupUser/VisibleUserId/VisibleUserIdentifier');  
my $uidValue   = user_id_from_barcode($id);

if (!defined($uidValue) || (ref($uidValue) && reftype($uidValue) eq 'HASH')) {
        do_lookup_user_error_stanza("PATRON_NOT_FOUND");
	die;
}

my ($propername,$email,$good_until,$userprivid) = ("name here","","good until","0") ;
            
my $patron = flesh_user($uidValue);
            
#if (blessed($patron)) {
	if ($patron->deleted eq 't') {
		do_lookup_user_error_stanza("PATRON_DELETED");
                die;
        }
	$propername = $patron->first_given_name . " " . $patron->family_name;

        if ( defined($patron->email) ) {
	$email = qq(
	        <UserAddressInformation>
                        <ElectronicAddress>
                                <ElectronicAddressType>
                                        <Scheme datatype="string">http:/blah.com</Scheme>
                                        <Value datatype="string">mailto</Value>
                                </ElectronicAddressType>
                                <ElectronicAddressData datatype="string">).HTML::Entities::encode($patron->email).qq(</ElectronicAddressData>]
                        </ElectronicAddress>
                </UserAddressInformation>);
	}

        $good_until = $patron->expire_date || "unknown";
        $userprivid = $patron->profile;
        #my $homeOU = $patron->home_ou->name;
        my $userpriv = $patron->profile->name;

my $hd =            <<LOOKUPUSERRESPONSE;
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
           <UserIdentifierValue>$id</UserIdentifierValue>
       </UniqueUserId>
	<UserOptionalFields>
		<VisibleUserId>
			<VisibleUserIdentifierType>
				<Scheme datatype="string">http://blah.com</Scheme>
				<Value datatype="string">Barcode</Value>
			</VisibleUserIdentifierType>
			<VisibleUserIdentifier datatype="string">$id</VisibleUserIdentifier>
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
		</UserPrivilege> $email
	</UserOptionalFields>
   </LookupUserResponse>
</NCIPMessage>

LOOKUPUSERRESPONSE

logit($hd,(caller(0))[3]);
}


sub fail {
my $error_msg = shift || "THIS IS THE DEFAULT NCIP RESP MSG";
print "Content-type: text/xml\n\n";

print <<ITEMREQ; 
<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <ItemRequestedResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>http://scheme.server.here/IRCIRCD?target=get_scheme_values&amp;scheme=UniqueAgencyId</Scheme>
                    <Value></Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>http://scheme.server.here/IRCIRCD?target=get_scheme_values&amp;scheme=UniqueAgencyId</Scheme>
                    <Value>$error_msg</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
    </ItemRequestedResponse>
</NCIPMessage>

ITEMREQ
}

sub do_lookup_user_error_stanza {

my $error = shift;
my $hd = <<LOOKUPPROB;
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
        <Problem>
                <ProcessingError>
                        <ProcessingErrorType>
                                <Scheme>http://www.niso.org/ncip/v1_0/schemes/processingerrortype/lookupuserprocessingerror.scm</Scheme>
                                <Value>$error</Value>
                        </ProcessingErrorType>
                        <ProcessingErrorElement>
                                <ElementName>AuthenticationInput</ElementName>
                        </ProcessingErrorElement></ProcessingError>
                </ProcessingError>
       </Problem>
</LookupUserResponse>
</NCIPMessage>

LOOKUPPROB

logit($hd,(caller(0))[3]);
}

# Login to the OpenSRF system/Evergreen.
#
# Returns a hash with the authtoken, authtime, and expiration (time in
# seconds since 1/1/1970).
sub login {

my $bootstrap = '/openils/conf/opensrf_core.xml';
my $uname = "USERNAMEHERE"; 
my $password = "PASSWORDHERE";
my $workstation = "REGISTEREDWORKSTATIONHERE";

# Bootstrap the client
OpenSRF::System->bootstrap_client(config_file => $bootstrap);
my $idl = OpenSRF::Utils::SettingsClient->new->config_value("IDL");
Fieldmapper->import(IDL => $idl);

# Initialize CStoreEditor:
OpenILS::Utils::CStoreEditor->init;

    my $seed = OpenSRF::AppSession
        ->create('open-ils.auth')
        ->request('open-ils.auth.authenticate.init', $uname)
        ->gather(1);

    return undef unless $seed;

    my $response = OpenSRF::AppSession
        ->create('open-ils.auth')
        ->request('open-ils.auth.authenticate.complete',
                  { username => $uname,
                    password => md5_hex($seed . md5_hex($password)),
                    type => 'staff' })
#                    workstation => $workstation })
        ->gather(1);

    return undef unless $response;

    my %result;
    $result{'authtoken'} = $response->{payload}->{authtoken};
    $result{'authtime'} = $response->{payload}->{authtime};
    $result{'expiration'} = time() + $result{'authtime'} if (defined($result{'authtime'}));
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
    if (time() > $session{'expiration'}) {
        %session = login();
        if (!%session) {
            die("Failed to reinitialize the session after expiration.");
        }
    }
}

# Retrieve the logged in user.
#
sub get_session {
    my $response = OpenSRF::AppSession->create('open-ils.auth')
        ->request('open-ils.auth.session.retrieve', $session{authtoken})->gather(1);
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
    if (time() < $session{'expiration'}) {
        my $response = OpenSRF::AppSession
            ->create('open-ils.auth')
            ->request('open-ils.auth.session.delete', $session{authtoken})
            ->gather(1);
        if ($response) {
        #    fail("Logout successful. Good-bye.\n");
	# strong.silent.success
            exit(0);
        } else {
            fail("Logout unsuccessful. Good-bye, anyway.");
        }
    }
}

sub update_copy {
    check_session_time();
    my ($copy,$status_id) = @_;
    my $e = new_editor(authtoken=>$session{authtoken});
    return $e->event->{textcode} unless ($e->checkauth);
    $e->xact_begin;
    $copy->status($status_id);
    return $e->event unless $e->update_asset_copy($copy);
    $e->commit;
    return 'SUCCESS';
}

# my paranoia re barcode on shipped items using visid for unique value
sub update_copy_shipped {
    check_session_time();
    my ($copy,$status_id,$barcode) = @_;
    my $e = new_editor(authtoken=>$session{authtoken});
    return $e->event->{textcode} unless ($e->checkauth);
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

    my $e = new_editor(authtoken=>$session{authtoken});
    return $e->event->{textcode} unless ($e->checkauth);

    # Get the calnumber
    my $vol = $e->retrieve_asset_call_number($copy->call_number);
    return $e->event->{textcode} unless ($vol);

    # Get the biblio.record_entry
    my $bre = $e->retrieve_biblio_record_entry($vol->record);
    return $e->event->{textcode} unless ($bre);

    # Delete everything in a transaction and rollback if anything fails.
    $e->xact_begin;
    my $r; # To hold results of editor calls
    $r = $e->delete_asset_copy($copy);
    unless ($r) {
        my $lval = $e->event->{textcode};
        $e->rollback;
        return $lval;
    }
    my $list = $e->search_asset_copy({call_number => $vol->id, deleted => 'f'});
    unless (@$list) {
        $r = $e->delete_asset_call_number($vol);
        unless ($r) {
            my $lval = $e->event->{textcode};
            $e->rollback;
            return $lval;
        }
        $list = $e->search_asset_call_number({record => $bre->id, deleted => 'f'});
        unless (@$list) {
            $bre->deleted('t');
            $r = $e->update_biblio_record_entry($bre);
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
    my $response = OpenSRF::AppSession->create('open-ils.search')
        ->request('open-ils.search.asset.copy.find_by_barcode', $barcode)
        ->gather(1);
    return $response;
}

sub locid_from_barcode {
    my ($barcode) = @_;
    my $response = OpenSRF::AppSession->create('open-ils.search')
        ->request('open-ils.search.biblio.find_by_barcode', $barcode)
        ->gather(1);
    return $response->{ids}[0];
}

# Convert a MARC::Record to XML for Evergreen
#
# Stolen from Dyrcona's issa framework which copied
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
    (my $xml = $input->as_xml_record()) =~ s/\n//sog;
    $xml =~ s/^<\?xml.+\?\s*>//go;
    $xml =~ s/>\s+</></go;
    $xml =~ s/\p{Cc}//go;
    $xml = OpenILS::Application::AppUtils->entityize($xml);
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
    my ($title, $callnumber, $barcode, $copy_status_id) = @_;

    my $e = new_editor(authtoken=>$session{authtoken});
    return $e->event->{textcode} unless ($e->checkauth);

    my $r = $e->allowed(['CREATE_COPY', 'CREATE_MARC', 'CREATE_VOLUME']);
    if (ref($r) eq 'HASH') {
        return $r->{textcode} . ' ' . $r->{ilsperm};
    }

    # Check if the barcode exists in asset.copy and bail if it does.
    my $list = $e->search_asset_copy({deleted => 'f', barcode => $barcode});
    if (@$list) {
# can we update it, if it exists? only if it is an INN-Reach status item
        $e->finish;
        fail('BARCODE_EXISTS');
    }

    # Create MARC record
    my $record = MARC::Record->new();
    $record->encoding('UTF-8');
    $record->leader('00881nam a2200193 4500');
    my $datespec = strftime("%Y%m%d%H%M%S.0", localtime);
    my @fields = ();
    push(@fields, MARC::Field->new('005', $datespec));
    push(@fields, MARC::Field->new('082', '0', '4', 'a' => $callnumber));
    push(@fields, MARC::Field->new('245', '0', '0', 'a' => $title));
    $record->append_fields(@fields);

    # Convert the record to XML
    my $xml = convert2marcxml($record);

    my $bre = OpenSRF::AppSession->create('open-ils.cat')
        ->request('open-ils.cat.biblio.record.xml.import', $session{authtoken}, $xml, 'System Local', 1)
        ->gather(1);
    return $bre->{textcode} if (ref($bre) eq 'HASH');

    # Create volume record
    my $vol = OpenSRF::AppSession->create('open-ils.cat')
        ->request('open-ils.cat.call_number.find_or_create', $session{authtoken}, $callnumber, $bre->id, 10)
        ->gather(1);
    return $vol->{textcode} if ($vol->{textcode});

    # Retrieve the user
    my $user = get_session;
    # Create copy record
    my $copy = Fieldmapper::asset::copy->new();
    $copy->barcode($barcode);
    $copy->call_number($vol->{acn_id});
    $copy->circ_lib(10);
    $copy->circulate('t');
    $copy->holdable('t');
    $copy->opac_visible('t');
    $copy->deleted('f');
    $copy->fine_level(2);
    $copy->loan_duration(2);
    $copy->location(1);
    $copy->status($copy_status_id);
    $copy->editor('1002741');
    $copy->creator('1002741');

    # Add the configured stat cat entries.
    #my @stat_cats;
    #my $nodes = $xpath->find("stat_cat_entry");
    #foreach my $node ($nodes->get_nodelist) {
    #    next unless ($node->isa('XML::XPath::Node::Element'));
    #    my $stat_cat_id = $node->getAttribute('stat_cat');
    #    my $value = $node->string_value();
    #    # Need to search for an existing asset.stat_cat_entry
        my $asce = $e->search_asset_stat_cat_entry({'stat_cat' => $stat_cat_id, 'value' => $value})->[0];
    #    unless ($asce) {
    #        # if not, create a new one and use its id.
    #        $asce = Fieldmapper::asset::stat_cat_entry->new();
    #        $asce->stat_cat($stat_cat_id);
    #        $asce->value($value);
    #        $asce->owner($ou->id);
    #        $e->xact_begin;
    #        $asce = $e->create_asset_stat_cat_entry($asce);
    #        $e->xact_commit;
    #    }
    #    push(@stat_cats, $asce);
    #}

    $e->xact_begin;
    $copy = $e->create_asset_copy($copy);
    #if (scalar @stat_cats) {
    #    foreach my $asce (@stat_cats) {
    #        my $ascecm = Fieldmapper::asset::stat_cat_entry_copy_map->new();
    #        $ascecm->stat_cat($asce->stat_cat);
    #        $ascecm->stat_cat_entry($asce->id);
    #        $ascecm->owning_copy($copy->id);
    #        $ascecm = $e->create_asset_stat_cat_entry_copy_map($ascecm);
    #    }
    #}
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
sub checkout
{
    check_session_time();
    my ($copy_barcode, $patron_barcode, $due_date) = @_;

    # Check for copy:
    my $copy = copy_from_barcode($copy_barcode);
    unless (defined($copy) && blessed($copy)) {
        return 'COPY_BARCODE_NOT_FOUND';
    }

    # Check for user
    my $uid = user_id_from_barcode($patron_barcode);
    return 'PATRON_BARCODE_NOT_FOUND' if (ref($uid));

    my $response = OpenSRF::AppSession->create('open-ils.circ')
        ->request('open-ils.circ.checkout.full.override', $session{authtoken},
                  { copy_barcode => $copy_barcode,
                    patron_barcode => $patron_barcode,
		    due_date => $due_date })
        ->gather(1);
    return $response->{textcode};
}

sub renewal
{
    check_session_time();
    my ($copy_barcode, $due_date) = @_;

    # Check for copy:
    my $copy = copy_from_barcode($copy_barcode);
    unless (defined($copy) && blessed($copy)) {
        return 'COPY_BARCODE_NOT_FOUND';
    }


    my $response = OpenSRF::AppSession->create('open-ils.circ')
        ->request('open-ils.circ.renew', $session{authtoken},
                  { copy_barcode => $copy_barcode,
		    due_date => $due_date })
        ->gather(1);
    return $response->{textcode};
}

# Check a copy in at an org_unit
#
# Arguments
# copy barcode
# org_unit
#
# Returns
# "SUCCESS" on success
# textcode of a failed OSRF request
# 'COPY_NOT_CHECKED_OUT' when the copy is not checked out or not
# checked out to the user's work_ou
sub checkin
{
    check_session_time();
    my ($barcode, $where) = @_;

    my $copy = copy_from_barcode($barcode);
    return $copy->{textcode} unless (blessed $copy);

    return 'COPY_NOT_CHECKED_OUT' unless ($copy->status == OILS_COPY_STATUS_CHECKED_OUT);

    my $e = new_editor(authtoken=>$session{authtoken});
    return $e->event->{textcode} unless ($e->checkauth);

    my $circ = $e->search_action_circulation([ { target_copy => $copy->id, xact_finish => undef } ])->[0];
    #return 'COPY_NOT_CHECKED_OUT' unless ($circ->circ_lib == $where->id);
    return 'COPY_NOT_CHECKED_OUT' unless ($circ->circ_lib == 10);

    my $r = OpenSRF::AppSession->create('open-ils.circ')
        ->request('open-ils.circ.checkin', $session{authtoken}, { barcode => $barcode, void_overdues => 1 })
        ->gather(1);
    return 'SUCCESS' if ($r->{textcode} eq 'ROUTE_ITEM');
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

    my $e = new_editor(authtoken=>$session{authtoken});
    return $response unless ($e->checkauth);

    my $card = $e->search_actor_card({barcode => $barcode, active => 't'});
    return $e->event unless($card);

    $response = $card->[0]->usr if (@$card);

    $e->finish;

    return $response;
}

# Place a hold for a patron.
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

sub place_simple_hold {
    check_session_time();
    #my ($type, $target, $patron, $pickup_ou) = @_;
    my ($target, $patron) = @_;
	# NOTE : switch "t" to an "f" to make inactive hold active
	require '/usr/src/rel_2_1/Open-ILS/src/support-scripts/oils_header.pl';
	use vars qw/ $apputils $memcache $user $authtoken $authtime /;
	osrf_connect("/openils/conf/opensrf_core.xml");
        oils_login("USERNAMEHERE", "PASSWORDHERE");
	my $full_hold = '{"__c":"ahr","__p":[null,null,null,null,1,null,null,null,null,"T",null,null,"","3",null,"3",null,"'.$patron.'",1,"3","'.$target.'","'.$patron.'",null,null,null,null,null,null,"f",null]}';
	my $f_hold_perl = OpenSRF::Utils::JSON->JSON2perl($full_hold);
	my $resp = simplereq(CIRC(), 'open-ils.circ.holds.create', $authtoken, $f_hold_perl );
	#oils_event_die($resp);
	my $errors= "";
	if (ref($resp) eq 'ARRAY' ) {
			($errors .= "error : ".$_->{textcode}) for @$resp;
			return $errors;
	}
	elsif (ref($resp) ne 'HASH' )  { return "Hold placed! hold_id = ". $resp ."\n" }
}

# Place a hold for a patron.
#
# Arguments
# Type of hold
# Target object appropriate for type of hold
# Patron for whom the hold is place
# OU where hold is to be picked up
#
# Returns
# "SUCCESS" on success
# textcode of a failed OSRF request
# "HOLD_TYPE_NOT_SUPPORTED" if the hold type is not supported
# (Currently only support 'T' and 'C')
sub place_hold {
    check_session_time();
    my ($type, $target, $patron, $pickup_ou) = @_;

    my $ou = org_unit_from_shortname($work_ou); # $work_ou is global
    my $ahr = Fieldmapper::action::hold_request->new;
    $ahr->hold_type($type);
    if ($type eq 'C') {
        # Check if we own the copy.
        if ($ou->id == $target->circ_lib) {
            # We own it, so let's place a copy hold.
            $ahr->target($target->id);
            $ahr->current_copy($target->id);
        } else {
            # We don't own it, so let's place a title hold instead.
            my $bib = bre_from_barcode($target->barcode);
            $ahr->target($bib->id);
            $ahr->hold_type('T');
        }
    } elsif ($type eq 'T') {
        $ahr->target($target);
    } else {
        return "HOLD_TYPE_NOT_SUPPORTED";
    }
    $ahr->usr($patron->id);
    $ahr->pickup_lib($pickup_ou->id);
    if (!$patron->email) {
        $ahr->email_notify('f');
        $ahr->phone_notify($patron->day_phone) if ($patron->day_phone);
    } else {
        $ahr->email_notify('t');
    }

    # We must have a title hold and we want to change the hold
    # expiration date if we're sending the copy to the VC.
    set_title_hold_expiration($ahr) if ($ahr->pickup_lib == $ou->id);

    my $params = { pickup_lib => $ahr->pickup_lib, patronid => $ahr->usr, hold_type => $ahr->hold_type };

    if ($ahr->hold_type eq 'C') {
        $params->{copy_id} = $ahr->target;
    } else {
        $params->{titleid} = $ahr->target;
    }

    my $r = OpenSRF::AppSession->create('open-ils.circ')
        ->request('open-ils.circ.title_hold.is_possible', $session{authtoken}, $params)
            ->gather(1);

    if ($r->{textcode}) {
        return $r->{textcode};
    } elsif ($r->{success}) {
        $r = OpenSRF::AppSession->create('open-ils.circ')
            ->request('open-ils.circ.holds.create.override', $session{authtoken}, $ahr)
                ->gather(1);

        my $returnValue = "SUCCESS";
        if (ref($r) eq 'HASH') {
            $returnValue = ($r->{textcode} eq 'PERM_FAILURE') ? $r->{ilsperm} : $r->{textcode};
            $returnValue =~ s/\.override$// if ($r->{textcode} eq 'PERM_FAILURE');
        }
        return $returnValue;
    } else {
        return 'HOLD_NOT_POSSIBLE';
    }
}

# Set the expiration date on title holds
#
# Argument
# Fieldmapper action.hold_request object
#
# Returns
# Nothing
sub set_title_hold_expiration {
    my $hold = shift;
    if ($title_holds->{unit} && $title_holds->{duration}) {
        my $expiration = DateTime->now(time_zone => $tz);
        $expiration->add($title_holds->{unit} => $title_holds->{duration});
        $hold->expire_time($expiration->iso8601());
    }
}

# Get actor.org_unit from the shortname
#
# Arguments
# org_unit shortname
#
# Returns
# Fieldmapper aou object
# or HASH on error
sub org_unit_from_shortname {
    check_session_time();
    my ($shortname) = @_;
    my $ou = OpenSRF::AppSession->create('open-ils.actor')
        ->request('open-ils.actor.org_unit.retrieve_by_shortname', $shortname)
        ->gather(1);
    return $ou;
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
    my $response = OpenSRF::AppSession->create('open-ils.actor')
        ->request('open-ils.actor.user.fleshed.retrieve', $session{'authtoken'}, $id,
                   [ 'card', 'cards', 'standing_penalties', 'home_ou', 'profile' ])
        ->gather(1);
    return $response;
}
