#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Get talk,SMS,data usage from fido website"""

import argparse
import json
import sys

import requests


def get_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument('-n', '--number', dest='number', required=True,
                        help='phone number')
    parser.add_argument('-p', '--password', dest='password', required=False,
                        help='fido password')
    parser.add_argument('-i', '--influxdb', dest='influxdb', required=False,
                        action='store_true', help='influxdb format', default=False)

    return parser.parse_args()

class FidoConnection(object):
    """Class to connect and get data from fido"""
    def __init__(self, options):
        self.options = options
        # Header
        self.headers = {}
        self.headers['User-Agent'] = ('Mozilla/5.0 (X11; Linux x86_64; rv:10.0.7) '
                                      'Gecko/20100101 Firefox/10.0.7 Iceweasel/10.0.7')
        # Cookies
        self.cookies = {}
        # Account number
        self.account_number = None
        # balance
        self.balance = None
        # fido dollar
        self.fido_dollar = None
        # usage
        self.usage = {}
        # data
        self.metrics = {}

    def _authenticate(self):
        """Log in fido website"""
        ##########################################################################################
        url = "https://rogers-fido.janraincapture.com/widget/traditional_signin.jsonp"
        capture = "bxpgwszl8jgtooke73nbwfyul2f84tp2ffqxx8bb"
        data = {
            "utf8": "✓",
            "capture_screen": "signIn",
            "js_version": "ccadba4",
            #"capture_transactionId": "bxpgwszl8jgtooke73nbwfyul2f84tp2ffqxx8kb",
            "capture_transactionId": capture,
            "form": "signInForm",
            "flow": "fido",
            "client_id": "bfkecrvys7sprse8kc4wtwugr2bj9hmp",
            "redirect_uri": "https://www.fido.ca/pages/#/",
            "response_type": "token",
            "flow_version": "d707d6bc-625a-40fa-9f95-aff1c1dbe1dd",
            "settings_version": "",
            "locale": "fr",
            "recaptchaVersion": "1",
            "userID": self.options.number,
            "currentPassword": self.options.password,
        }
        raw_res = requests.post(url, headers=self.headers, data=data)
        self.cookies = raw_res.cookies

        ##########################################################################################
        url = "https://rogers-fido.janraincapture.com/widget/get_result.jsonp"
        params = {"transactionId": capture}
        raw_res = requests.get(url, params=params, headers=self.headers, cookies=self.cookies)

        return_data = json.loads(raw_res.text[43:-2])

        if "result" not in return_data or "accessToken" not in return_data['result']:
            print "Error during login"
            sys.exit(1)

        ##########################################################################################
        url = "https://www.fido.ca/pages/api/selfserve/v3/login"
        data = {"accessToken": return_data['result']['accessToken'],
                "uuid": return_data['result']['userData']['uuid'],
               }
        raw_res = requests.post(url, headers=self.headers, data=data)
        self.cookies = raw_res.cookies
        output = raw_res.json()
        try:
            self.account_number = output['getCustomerAccounts']['accounts'][0]['accountNumber']
        except (KeyError, IndexError):
            print "Error during login"
            sys.exit(2)

    def get_fido_dollar(self):
        """Fido dollar"""
        url = "https://www.fido.ca/pages/api/selfserve/v1/wireless/rewards/basicinfo"
        data = json.dumps({"fidoDollarBalanceFormList":[{"phoneNumber": self.options.number,
                                                         "accountNumber": self.account_number}]})
        headers_json = self.headers.copy()
        headers_json["Content-Type"] = "application/json;charset=UTF-8"
        raw_res = requests.post(url, cookies=self.cookies, headers=headers_json, data=data)
        output = raw_res.json()
        try:
            self.fido_dollar = float(output.get("fidoDollarBalanceInfoList", [{}])[0]\
                                     .get("fidoDollarBalance", None))
        except TypeError:
            self.fido_dollar = None

    def get_balance(self):
        """Get balance"""
        url = "https://www.fido.ca/pages/api/selfserve/v2/accountOverview"
        data = {"ctn": self.options.number,
                "language": "fr",
                "accountNumber": self.account_number,
               }
        raw_res = requests.post(url, cookies=self.cookies, headers=self.headers, data=data)
        output = raw_res.json()
        try:
            self.balance = float(output.get("getAccountInfo", {}).get("balance", None))
        except TypeError:
            self.balance = None

    def get_usage(self):
        """Get usage"""
        usage_url = "https://www.fido.ca/pages/api/selfserve/v1/postpaid/dashboard/usage"
        data = {"ctn": self.options.number,
                "language": "fr",
                "accountNumber": self.account_number}
        raw_res = requests.post(usage_url, cookies=self.cookies, data=data, headers=self.headers)
        self.usage = raw_res.json()

    def prepare_data(self):
        """Read data from usage/balance/fido_dollar"""
        self.metrics = {}
        json_key = 'wirelessUsageSummaryInfoList'
        for mtype in ['used', 'total', 'remaining']:
            # TODO: handle multiple phone number
            # self.usage['data'][0] means the first phone number of your account...
            # How to link phone number to this index ???
            self.metrics['data_' + mtype] = self.usage['data'][0][json_key][0][mtype]
            self.metrics['talk_' + mtype] = self.usage['talk'][0][json_key][0][mtype]
            self.metrics['mms_' + mtype] = self.usage['text'][0][json_key][0][mtype]
            self.metrics['sms_' + mtype] = self.usage['text'][0][json_key][1][mtype]
            self.metrics['smsint_' + mtype] = self.usage['text'][0][json_key][2][mtype]

        if self.fido_dollar is not None:
            self.metrics['fido_dollar'] = self.fido_dollar
        if self.balance is not None:
            self.metrics['balance'] = self.balance

    def print_output(self):
        """Print using json or influxdb format"""
        if self.options.influxdb:
            lines = []
            # Add data
            line = "data,unit=bytes "
            line += ",".join(["{}={}".format(field, self.metrics['data_' + field])
                              for field in ['used', 'total', 'remaining']
                              if self.metrics['data_' + field] >= 0])
            lines.append(line)
            # Add talk
            line = "talk,unit=minutes "
            line += ",".join(["{}={}".format(field, self.metrics['talk_' + field])
                              for field in ['used', 'total', 'remaining']
                              if self.metrics['talk_' + field] >= 0])
            lines.append(line)

            # Add messages
            for msg_type in ['mms', 'sms', 'smsint']:
                line = "messages,type={} ".format(msg_type)
                line += ",".join(["{}={}".format(field, self.metrics[msg_type + '_' + field])
                                  for field in ['used', 'total', 'remaining']
                                  if self.metrics[msg_type + '_' + field] >= 0])
                lines.append(line)
            # Add balance
            line = "balance,unit=$ balance={},fido_dollar={}".format(self.metrics['balance'],
                                                                     self.metrics['fido_dollar'])
            lines.append(line)
            print "\n".join(lines)
        else:
            print json.dumps(self.metrics)

    def connect(self):
        """Connect to fido website"""
        self._authenticate()


def main():
    """Main function"""
    conn = FidoConnection(get_args())
    conn.connect()
    conn.get_balance()
    conn.get_fido_dollar()
    conn.get_usage()
    conn.prepare_data()
    conn.print_output()

if __name__ == '__main__':
    main()
