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

# read in last xml request
{
  local $/ = undef;
  open FILE, "last_post.txt" or die "Couldn't open file: $!";
  binmode FILE;
  $prev_xml = <FILE>;
  close FILE;
}

# fail as gracefully as possible if repeat post has occured  
if ( $xml eq $prev_xml ) {
	fail("DUPLICATE NCIP REQUEST POSTED!");
}

# save just the last post in order to test diff on the next request 
open LAST_POST_DATA, ">last_post.txt";
print LAST_POST_DATA $xml;
close LAST_POST_DATA;

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
                                                		  ( $doc->exists('/NCIPMessage/RenewItem') ? renew_item() :
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
                )
        );

    # Clear any SIGALRM timers.
    alarm(0);
    logout();
} else {
    fail("Unable to perform action : Unknown Service Request");
}

sub logit {
	my ($msg,$func,$more_info) = @_;
	open RESP_DATA, ">>resp_data.txt";
	print RESP_DATA $msg;
	print RESP_DATA $more_info unless !$more_info; 
	close RESP_DATA;
	print $msg || fail($func);
}

sub staff_log {
	my ($taiv,$faiv,$more_info) = @_;
	my $now = localtime();
	open STAFF_LOG, ">>staff_data.csv";
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

	my $pid         = $doc->findvalue('/NCIPMessage/ItemRenewed/UniqueUserId/UserIdentifierValue');  
	my $visid      = $doc->findvalue('/NCIPMessage/ItemRenewed/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier').$faidValue;  
	my $due_date   = $doc->findvalue('/NCIPMessage/ItemRenewed/DateDue');  
	
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
Desired Due Date 	= $due_date

MOREINFO

	logit($hd,(caller(0))[3],$more_info);
staff_log($taidValue,$faidValue,"ItemRenewal -> Patronid : ".$pid." | Visid : ".$visid." | Due Date : ".$due_date);
}


sub renew_item {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/RenewItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/RenewItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/RenewItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/RenewItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

	my $pid         = $doc->findvalue('/NCIPMessage/RenewItem/UniqueUserId/UserIdentifierValue');  
	my $barcode    = $doc->findvalue('/NCIPMessage/RenewItem/UniqueItemId/ItemIdentifierValue');  
	my $due_date   = $doc->findvalue('/NCIPMessage/RenewItem/DateDue');  
	
	my $r = renewal($barcode,$due_date);

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
            <ItemIdentifierValue datatype="string">$visid</ItemIdentifierValue>
        </UniqueItemId>
    </RenewItemResponse>
</NCIPMessage> 

ITEMRENEWAL

my $more_info = <<MOREINFO;

VISID 			= $visid
Desired Due Date 	= $due_date

MOREINFO

	logit($hd,(caller(0))[3],$more_info);
staff_log($taidValue,$faidValue,"RenewItem -> Patronid : ".$pid." | Visid : ".$visid." | Due Date : ".$due_date);
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
	my $patron     = $doc->findvalue('/NCIPMessage/AcceptItem/UserOptionalFields/VisibleUserId/VisibleUserIdentifier');  
	my $copy = copy_from_barcode($visid);
	my $r2 = update_copy($copy,111); # put into INN-Reach Hold status

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
staff_log($taidValue,$faidValue,"AcceptItem -> Request Id : ".$request_id." | Patron Id : ".$patron." | Visible Id :".$visid);
}

sub item_received {
     my $faidValue  = $doc->find('/NCIPMessage/ItemReceived/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
     my $barcode      = $doc->findvalue('/NCIPMessage/ItemReceived/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier').$faidValue;  
     my $copy = copy_from_barcode($barcode);
     fail($copy->{textcode}." $barcode") unless (blessed $copy);
     my $r1 = checkin($barcode) if ($copy->status == OILS_COPY_STATUS_CHECKED_OUT); # checkin the item before delete if ItemCheckedIn step was skipped
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
staff_log($taidValue,$faidValue,"ItemReceived -> Barcode : ".$barcode);
}

sub item_cancelled {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/ItemRequestCancelled/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  

	my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/ItemRequestCancelled/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  
	my $UniqueItemIdAgencyIdValue  = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/UniqueItemId/UniqueAgencyId/Value');  

	my $barcode = $doc->findvalue('/NCIPMessage/ItemRequestCancelled/UniqueItemId/ItemIdentifierValue');  

	if ( $barcode =~ /^i/ ) { # delete copy only if barcode is an iNUMBER
		$barcode .= $faidValue;
     		my $copy = copy_from_barcode($barcode);
     		fail($copy->{textcode}." $barcode") unless (blessed $copy);
     		my $r = delete_copy($copy);
	} 
	else {
		# remove hold!
     		my $copy = copy_from_barcode($barcode);
     		fail($copy->{textcode}." $barcode") unless (blessed $copy);
		my $r = update_copy($copy,0); # available vs. remove hold? need to test further ...
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
staff_log($taidValue,$faidValue,"ItemRequestCancelled -> Barcode : ".$barcode);
}

sub item_checked_in {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedIn/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/ItemCheckedIn/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedIn/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/ItemCheckedIn/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

     	my $barcode      = $doc->findvalue('/NCIPMessage/ItemCheckedIn/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier').$faidValue;  
     	my $r = checkin($barcode);  
     	my $copy = copy_from_barcode($barcode);
	fail($copy->{textcode}." $barcode") unless (blessed $copy);
     	my $r2 = update_copy($copy,113); # "INN-Reach Transit Return" status

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
staff_log($taidValue,$faidValue,"ItemCheckedIn -> Barcode : ".$barcode);
}

sub item_checked_out {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedOut/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/ItemCheckedOut/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemCheckedOut/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/ItemCheckedOut/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

	my $pid         = $doc->findvalue('/NCIPMessage/ItemCheckedOut/UserOptionalFields/VisibleUserId/VisibleUserIdentifier');  
	my $due_date   = $doc->findvalue('/NCIPMessage/ItemCheckedOut/DateDue');  
	my $visid    = $doc->findvalue('/NCIPMessage/ItemCheckedOut/ItemOptionalFields/ItemDescription/VisibleItemId/VisibleItemIdentifier').$faidValue;  

	my $copy = copy_from_barcode($visid);
	fail($copy->{textcode}." $visid") unless (blessed $copy);
	my $r = update_copy($copy,0); # seemed like copy had to be available before it could be checked out, so ...
     	my $r1 = checkin($visid) if ($copy->status == OILS_COPY_STATUS_CHECKED_OUT); # double posted itemcheckedout messages cause error ... trying to simplify 
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

	logit($hd,(caller(0))[3]);
	staff_log($taidValue,$faidValue,"ItemCheckedOut -> Visible Id : ".$visid." | Patron Id : ".$pid." | Due Date : ".$due_date);
}

sub check_out_item {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/CheckOutItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/CheckOutItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/CheckOutItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/CheckOutItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

	my $mdate       = $doc->findvalue('/NCIPMessage/CheckOutItem/MandatedAction/DateEventOccurred');  
	my $id         = $doc->findvalue('/NCIPMessage/LookupUser/VisibleUserId/VisibleUserIdentifier');  
	my $pid   	= qq(zyyyy);

	my $barcode    = $doc->findvalue('/NCIPMessage/CheckOutItem/UniqueItemId/ItemIdentifierValue');  
	my $due_date   = $doc->findvalue('/NCIPMessage/CheckOutItem/DateDue');  

	my $copy = copy_from_barcode($barcode);
	fail($copy->{textcode}." $barcode") unless (blessed $copy);

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
staff_log($taidValue,$faidValue,"CheckOutItem -> Barcode : ".$barcode." | Patron Id : ".$pid." | Due Date : ".$due_date);
}

sub check_in_item {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/CheckInItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme = HTML::Entities::encode($faidSchemeX);
	my $faidValue  = $doc->find('/NCIPMessage/CheckInItem/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  
	my $taidSchemeX = $doc->findvalue('/NCIPMessage/CheckInItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme = HTML::Entities::encode($taidSchemeX);
	my $taidValue  = $doc->find('/NCIPMessage/CheckInItem/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  

     	my $barcode    = $doc->findvalue('/NCIPMessage/CheckInItem/UniqueItemId/ItemIdentifierValue');  
     	my $r = checkin($barcode);  
 	my $copy = copy_from_barcode($barcode);
	fail($copy->{textcode}." $barcode") unless (blessed $copy);
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
staff_log($taidValue,$faidValue,"CheckInItem -> Barcode : ".$barcode);
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
	fail($copy->{textcode}." $barcode") unless (blessed $copy);
	my $r = update_copy_shipped($copy,112,$visid); # put copy into INN-Reach Transit status & modify barcode = Visid != tempIIIiNumber

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
staff_log($taidValue,$faidValue,"ItemShipped -> Visible Id : ".$visid." | Barcode : ".$barcode." | Title : ".$title." | Call Number : ".$callnumber);
}

sub item_request {
	my $faidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequested/InitiationHeader/FromAgencyId/UniqueAgencyId/Scheme');  
	my $faidScheme 	= HTML::Entities::encode($faidSchemeX);
	my $faidValue  	= $doc->find('/NCIPMessage/ItemRequested/InitiationHeader/FromAgencyId/UniqueAgencyId/Value');  

	my $taidSchemeX = $doc->findvalue('/NCIPMessage/ItemRequested/InitiationHeader/ToAgencyId/UniqueAgencyId/Scheme');  
	my $taidScheme 	= HTML::Entities::encode($taidSchemeX);
	my $taidValue  	= $doc->find('/NCIPMessage/ItemRequested/InitiationHeader/ToAgencyId/UniqueAgencyId/Value');  
	my $UniqueItemIdAgencyIdValue  = $doc->findvalue('/NCIPMessage/ItemRequested/UniqueItemId/UniqueAgencyId/Value');  

	my $id         	= $doc->findvalue('/NCIPMessage/ItemRequested/UniqueUserId/UserIdentifierValue');  
	my $barcode    	= $doc->findvalue('/NCIPMessage/ItemRequested/UniqueItemId/ItemIdentifierValue'); 
	my $author    	= $doc->findvalue('/NCIPMessage/ItemRequested/ItemOptionalFields/BibliographicDescription/Author');  
	my $title    	= $doc->findvalue('/NCIPMessage/ItemRequested/ItemOptionalFields/BibliographicDescription/Title');  
	my $callnumber 	= $doc->findvalue('/NCIPMessage/ItemRequested/ItemOptionalFields/ItemDescription/CallNumber');  
	my $medium_type = $doc->find('/NCIPMessage/ItemRequested/ItemOptionalFields/BibliographicDescription/MediumType/Value');  

	my $r = "default error checking response"; 

	if ( $barcode =~ /^i/ ) { # create copy only if barcode is an iNUMBER
		my $copy_status_id = 110; # INN-Reach Loan Requested 
		$barcode .= $faidValue;
		$r = create_copy($title, $callnumber, $barcode, $copy_status_id, $medium_type);
    		my $pid = user_id_from_barcode($id);
        	my $localid = locid_from_barcode($barcode);
		my $r2 = place_simple_hold($localid, $pid);
	} 
	else {
	# place hold for patron_id 1013459 = zyyyy demo user 
        	my $localid = locid_from_barcode($barcode);
    		my $pid = "1013459";
		$r = place_simple_hold($localid, $pid);
		my $copy = copy_from_barcode($barcode);
		my $r2 = update_copy($copy,111); # put into INN-Reach Hold status
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
staff_log($taidValue,$faidValue,"ItemRequested -> Barcode : ".$barcode." | Title : ".$title." | Call Number : ".$callnumber." | ID :". $id);
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
        do_lookup_user_error_stanza("PATRON_NOT_FOUND : $id");
	die;
}

my ($propername,$email,$good_until,$userprivid, $block_stanza) = ("name here","","good until","0","") ; # defaults
            
my $patron = flesh_user($uidValue);
           
#if (blessed($patron)) {
	my $patron_ok = 1;
        my @penalties = @{$patron->standing_penalties};

	if ($patron->deleted eq 't') {
		do_lookup_user_error_stanza("PATRON_DELETED : $uidValue");
                die;
        } elsif ($patron->barred eq 't') {
                do_lookup_user_error_stanza("PATRON_BARRED : $uidValue");
                die;
        } elsif ($patron->active eq 'f') {
                do_lookup_user_error_stanza("PATRON_INACTIVE : $uidValue");
                die;
        } 

	elsif ($#penalties > -1) {
#                my $penalty;
#               	foreach $penalty (@penalties) {
#                    if (defined($penalty->standing_penalty->block_list)) {
#                            my @block_list = split(/\|/, $penalty->standing_penalty->block_list);
#                            foreach my $block (@block_list) {
#                                foreach my $block_on (@$block_types) {
#                                    if ($block eq $block_on) {
#                                        $block_stanza .= "\n".$penalty->standing_penalty->name;
#                                        $patron_ok = 0;
#                                    }
#                                    last unless ($patron_ok);
#        	                }
#                                last unless ($patron_ok);
#                  	    }
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

        if ( defined($patron->email) ) {
	     $email = qq(
	        <UserAddressInformation>
                        <ElectronicAddress>
                                <ElectronicAddressType>
                                        <Scheme datatype="string">http://testing.now</Scheme>
                                        <Value datatype="string">mailto</Value>
                                </ElectronicAddressType>
                                <ElectronicAddressData datatype="string">).HTML::Entities::encode($patron->email).qq(</ElectronicAddressData>
                        </ElectronicAddress>
                </UserAddressInformation>);
	}

	$propername = $patron->first_given_name . " " . $patron->family_name;
        $good_until = $patron->expire_date || "unknown";
        $userprivid = $patron->profile;
        my $userou  = $patron->home_ou->name;
        my $userpriv = $patron->profile->name;

#} else {
#	do_lookup_user_error_stanza("PATRON_NOT_FOUND : $id");
#	die;
#}
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
		</UserPrivilege> $email $block_stanza
	</UserOptionalFields>
   </LookupUserResponse>
</NCIPMessage>

LOOKUPUSERRESPONSE

logit($hd,(caller(0))[3]);
staff_log($taidValue,$faidValue,"LookupUser -> Patron Barcode : ".$id." | Patron Id : ".$uidValue." | User Name : ".$propername." | User Priv : ".$userpriv);
}


sub fail {
my $error_msg = shift || "THIS IS THE DEFAULT / DO NOT HANG III NCIP RESP MSG";
print "Content-type: text/xml\n\n";

print <<ITEMREQ; 
<!DOCTYPE NCIPMessage PUBLIC "-//NISO//NCIP DTD Version 1.0//EN" "http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
<NCIPMessage version="http://www.niso.org/ncip/v1_0/imp1/dtd/ncip_v1_0.dtd">
    <ItemRequestedResponse>
        <ResponseHeader>
            <FromAgencyId>
                <UniqueAgencyId>
                    <Scheme>http://136.181.125.166:6601/IRCIRCD?target=get_scheme_values&amp;scheme=UniqueAgencyId</Scheme>
                    <Value></Value>
                </UniqueAgencyId>
            </FromAgencyId>
            <ToAgencyId>
                <UniqueAgencyId>
                    <Scheme>http://136.181.125.166:6601/IRCIRCD?target=get_scheme_values&amp;scheme=UniqueAgencyId</Scheme>
                    <Value>$error_msg</Value>
                </UniqueAgencyId>
            </ToAgencyId>
        </ResponseHeader>
    </ItemRequestedResponse>
</NCIPMessage>

ITEMREQ

staff_log($taidValue,$faidValue,((caller(0))[3]." -> ".$error_msg));
die;
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
                        </ProcessingErrorElement>
                </ProcessingError>
       </Problem>
</LookupUserResponse>
</NCIPMessage>

LOOKUPPROB

logit($hd,(caller(0))[3]);
staff_log($taidValue,$faidValue,((caller(0))[3]." -> ".$error));
die;
}

# Login to the OpenSRF system/Evergreen.
#
# Returns a hash with the authtoken, authtime, and expiration (time in
# seconds since 1/1/1970).
sub login {

my $bootstrap = '/openils/conf/opensrf_core.xml';
my $uname = "STAFF_EQUIVALENT_USERNAME_HERE"; 
my $password = "STAFF_EQUIVALENT_PASSWORD";

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
    my ($title, $callnumber, $barcode, $copy_status_id, $medium_type) = @_;

    my $e = new_editor(authtoken=>$session{authtoken});
    return $e->event->{textcode} unless ($e->checkauth);

    my $r = $e->allowed(['CREATE_COPY', 'CREATE_MARC', 'CREATE_VOLUME']);
    if (ref($r) eq 'HASH') {
        return $r->{textcode} . ' ' . $r->{ilsperm};
    }

    # Check if the barcode exists in asset.copy and bail if it does.
    my $list = $e->search_asset_copy({deleted => 'f', barcode => $barcode});
    if (@$list) {
# in the future, can we update it, if it exists and only if it is an INN-Reach status item ?
        $e->finish;
        fail('BARCODE_EXISTS ! Barcode : '.$barcode);
	die;
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
        ->request('open-ils.cat.call_number.find_or_create', $session{authtoken}, $callnumber, $bre->id, 3)
        ->gather(1);
    return $vol->{textcode} if ($vol->{textcode});

    # Retrieve the user
    my $user = get_session;
    # Create copy record
    my $copy = Fieldmapper::asset::copy->new();
    $copy->circ_modifier(qq($medium_type));
    $copy->barcode($barcode);
    $copy->call_number($vol->{acn_id});
    $copy->circ_lib(3); # just testing with one circ_lib for now
    $copy->circulate('t');
    $copy->holdable('t');
    $copy->opac_visible('t');
    $copy->deleted('f');
    $copy->fine_level(2);
    $copy->loan_duration(2);
    $copy->location(1);
    $copy->status($copy_status_id);
    $copy->editor('1');
    $copy->creator('1');

    # Add the configured stat cat entries.
    #my @stat_cats;
    #my $nodes = $xpath->find("/copy/stat_cat_entry");
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
        return 'COPY_BARCODE_NOT_FOUND : '.$copy_barcode;
    }

    # Check for user
    my $uid = user_id_from_barcode($patron_barcode);
    return 'PATRON_BARCODE_NOT_FOUND : '.$patron_barcode if (ref($uid));

    my $response = OpenSRF::AppSession->create('open-ils.circ')
        ->request('open-ils.circ.checkout.full.override', $session{authtoken},
                  { copy_barcode => $copy_barcode,
                    patron_id => $uid, 
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
        return 'COPY_BARCODE_NOT_FOUND : '.$copy_barcode;
    }

    my $response = OpenSRF::AppSession->create('open-ils.circ')
        ->request('open-ils.circ.renew.override', $session{authtoken},
                  { copy_barcode => $copy_barcode,
		    due_date => $due_date })
        ->gather(1);
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
# 'COPY_NOT_CHECKED_OUT' when the copy is not checked out or not
# checked out to the user's work_ou

sub checkin
{
    check_session_time();
    my ($barcode) = @_;

    my $copy = copy_from_barcode($barcode);
    return $copy->{textcode} unless (blessed $copy);

    return ("COPY_NOT_CHECKED_OUT $barcode") unless ($copy->status == OILS_COPY_STATUS_CHECKED_OUT);

    my $e = new_editor(authtoken=>$session{authtoken});
    return $e->event->{textcode} unless ($e->checkauth);

    my $circ = $e->search_action_circulation([ { target_copy => $copy->id, xact_finish => undef } ])->[0];
    my $r = OpenSRF::AppSession->create('open-ils.circ')
        ->request('open-ils.circ.checkin.override', $session{authtoken}, { force => 1, copy_id => $copy->id})
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

sub place_simple_hold {
    check_session_time();
    #my ($type, $target, $patron, $pickup_ou) = @_;
    my ($target, $patron) = @_;
	# NOTE : switch "t" to an "f" to make inactive hold active
require '/home/opensrf/Evergreen-ILS-2.1.1/Open-ILS/src/support-scripts/oils_header.pl';
use vars qw/ $apputils $memcache $user $authtoken $authtime /;
	osrf_connect("/openils/conf/opensrf_core.xml");
        oils_login("STAFF_EQUIVALENT_USERNAME", "STAFF_EQUIVALENT_PASSWORD");
	my $full_hold = '{"__c":"ahr","__p":[null,null,null,null,1,null,null,null,null,"C",null,null,"","3",null,"3",null,"'.$patron.'",1,"3","'.$target.'","'.$patron.'",null,null,null,null,null,null,"f",null]}';
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
    $ahr->usr(user_id_from_barcode($id));
    #$ahr->pickup_lib($pickup_ou->id);
    $ahr->pickup_lib('3');
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
