from datetime import datetime, timedelta
from hashlib import sha1, sha256, md5
from hmac import new as hmac
from types import MethodType
import re

from domaintools._version import current as version
from domaintools.results import GroupedIterable, ParsedWhois, Reputation, Results

AVAILABLE_KEY_SIGN_HASHES = ['sha1', 'sha256', 'md5']


def delimited(items, character='|'):
    """Returns a character delimited version of the provided list as a Python string"""
    return character.join(items) if type(items) in (list, tuple, set) else items


class API(object):
    """Enables interacting with the DomainTools API via Python:

           from domaintools import API

           api = API('my_name', 'my_key')
           results = api.domain_search('domain')

        By default the API will automatically space out your requests to match your rate limits.
        If your running over multiple Python runtimes, have your own rate limiting approach, or are doing a one-off
        query (such as for a CLI command) you can set rate_limit=False to turn this feature off.

        If you encounter SSL errors you can pass in verify_ssl=False to avoid verification of the SSL cert.
        To use the API without SSL in it's entirety pass in https=False.

       For detailed usage information of all API calls see: https://www.domaintools.com/resources/api-documentation/
    """
    limits = {}
    limits_set = False

    def __init__(self, username, key, https=True, verify_ssl=True, rate_limit=True, proxy_url=None,
                 always_sign_api_key=False, key_sign_hash='sha256', app_name="python_wrapper", app_version=version,
                 **default_parameters):
        if not default_parameters:
            self.default_parameters = {}
        else:
            self.default_parameters = default_parameters
        self.username = username
        self.key = key
        self.https = https
        self.verify_ssl = verify_ssl
        self.rate_limit = rate_limit
        self.proxy_url = proxy_url
        self.extra_request_params = {}
        self.always_sign_api_key = always_sign_api_key
        self.key_sign_hash = key_sign_hash
        self.default_parameters['app_name'] = app_name
        self.default_parameters['app_version'] = app_version
        if not https:
            raise Exception("The DomainTools API endpoints no longer support http traffic. Please make sure https=True.")
        if proxy_url:
            if isinstance(proxy_url, str):
                self.proxy_url = {'http://': proxy_url, 'https://': proxy_url}
            else:
                raise Exception("Proxy URL must be a string. For example: '127.0.0.1:8888'")

    def _rate_limit(self):
        """Pulls in and enforces the latest rate limits for the specified user"""
        self.limits_set = True
        for product in self.account_information():
            limit_minutes = product['per_minute_limit'] or None
            limit_hours = product['per_hour_limit'] or None

            default = 3600 
            hours = limit_hours and 3600 / float(limit_hours)
            minutes = limit_minutes and 60 / float(limit_minutes)

            self.limits[product['id']] = {'interval': timedelta(
                seconds = minutes or hours or default 
            )
        }

    def _results(self, product, path, cls=Results, **kwargs):
        """Returns _results for the specified API path with the specified **kwargs parameters"""
        if product != 'account-information' and self.rate_limit and not self.limits_set and not self.limits:
            self._rate_limit()

        uri = '/'.join(('https://api.domaintools.com', path.lstrip('/')))
        parameters = self.default_parameters.copy()
        parameters['api_username'] = self.username
        self.handle_api_key(path, parameters)
        parameters.update(
            {
                key: str(value).lower() if value in (True, False) else value
                for key, value in kwargs.items()
                if value is not None
            }
        )

        return cls(self, product, uri, **parameters)

    def handle_api_key(self, path, parameters):
        if self.https and not self.always_sign_api_key:
            parameters['api_key'] = self.key
        else:
            if self.key_sign_hash and self.key_sign_hash in AVAILABLE_KEY_SIGN_HASHES:
                signing_hash = eval(self.key_sign_hash)
            else:
                raise ValueError("Invalid value '{0}' for 'key_sign_hash'. "
                                 "Values available are {1}".format(self.key_sign_hash, ','.join(AVAILABLE_KEY_SIGN_HASHES)))
            parameters['timestamp'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            parameters['signature'] = hmac(self.key.encode('utf8'),
                                           ''.join([self.username, parameters['timestamp'], path]).encode('utf8'),
                                           digestmod=signing_hash).hexdigest()

    def account_information(self, **kwargs):
        """Provides a snapshot of your accounts current API usage"""
        return self._results('account-information', '/v1/account', items_path=('products',), **kwargs)

    def available_api_calls(self):
        """Provides a list of api calls that you can use based on your account information."""

        def snakecase(string):
            string = re.sub(r"[\-\.\s]", '_', str(string))
            if not string:
                return string
            return str(string[0]).lower() + re.sub(r"[A-Z]", lambda matched: '_' + str(matched.group(0)).lower(),
                                                   string[1:])

        api_calls = tuple((api_call for api_call in dir(API) if not api_call.startswith('_') and
                           callable(getattr(API, api_call, None))))
        return sorted([snakecase(p["id"]) for p in self.account_information()["products"] if snakecase(p["id"]) in api_calls])

    def brand_monitor(self, query, exclude=None, domain_status=None, days_back=None, **kwargs):
        """Pass in one or more terms as a list or separated by the pipe character ( | )"""
        if exclude is None:
            exclude = []
        return self._results('mark-alert', '/v1/mark-alert', query=delimited(query), exclude=delimited(exclude),
                             domain_status=domain_status, days_back=days_back, items_path=('alerts',), **kwargs)

    def domain_profile(self, query, **kwargs):
        """Returns a profile for the specified domain name"""
        return self._results('domain-profile', '/v1/{0}'.format(query))

    def domain_search(self, query, exclude_query=None, max_length=25, min_length=2, has_hyphen=True, has_number=True,
                      active_only=False, deleted_only=False, anchor_left=False, anchor_right=False, page=1, **kwargs):
        """Each term in the query string must be at least three characters long.
           Pass in a list or use spaces to separate multiple terms.
        """
        if exclude_query is None:
            exclude_query = []
        return self._results('domain-search', '/v2/domain-search', query=delimited(query, ' '),
                             exclude_query=delimited(exclude_query, ' '),
                             max_length=max_length, min_length=min_length, has_hyphen=has_hyphen, has_number=has_number,
                             active_only=active_only, deleted_only=deleted_only, anchor_left=anchor_left,
                             anchor_right=anchor_right, page=page, items_path=('results',), **kwargs)

    def hosting_history(self, query, **kwargs):
        """Returns the hosting history from the given domain name"""
        return self._results('hosting-history', '/v1/{0}/hosting-history'.format(query), cls=GroupedIterable, **kwargs)

    def ip_monitor(self, query, days_back=0, page=1, **kwargs):
        """Pass in the IP Address you wish to query ( i.e. 199.30.228.112 )."""
        return self._results('ip-monitor', '/v1/ip-monitor', query=query, days_back=days_back, page=page,
                             items_path=('alerts',), **kwargs)

    def ip_registrant_monitor(self, query, days_back=0, search_type="all", server=None, country=None, org=None, page=1,
                              include_total_count=False, **kwargs):
        """Query based on free text query terms"""
        return self._results('ip-registrant-monitor', '/v1/ip-registrant-monitor', query=query,
                             days_back=days_back, search_type=search_type, server=server, country=country, org=org,
                             page=page, include_total_count=include_total_count, **kwargs)

    def name_server_monitor(self, query, days_back=0, page=1, **kwargs):
        """Pass in the hostname of the Name Server you wish to query ( i.e. dynect.net )."""
        return self._results('name-server-monitor', '/v1/name-server-monitor', query=query, days_back=days_back,
                             page=page, items_path=('alerts',), **kwargs)

    def parsed_whois(self, query, **kwargs):
        """Pass in a domain name"""
        return self._results('parsed-whois', '/v1/{0}/whois/parsed'.format(query), cls=ParsedWhois, **kwargs)

    def registrant_monitor(self, query, exclude=None, days_back=0, limit=None, **kwargs):
        """One or more terms as a Python list or separated by the pipe character ( | )."""
        if exclude is None:
            exclude = []
        return self._results('registrant-alert', '/v1/registrant-alert', query=delimited(query),
                             exclude=delimited(exclude), days_back=days_back, limit=limit, items_path=('alerts',),
                             **kwargs)

    def reputation(self, query, include_reasons=False, **kwargs):
        """Pass in a domain name to see its reputation score"""
        return self._results('reputation', '/v1/reputation', domain=query, include_reasons=include_reasons,
                             cls=Reputation, **kwargs)

    def reverse_ip(self, domain=None, limit=None, **kwargs):
        """Pass in a domain name."""
        return self._results('reverse-ip', '/v1/{0}/reverse-ip'.format(domain), limit=limit, **kwargs)

    def host_domains(self, ip=None, limit=None, **kwargs):
        """Pass in an IP address."""
        return self._results('reverse-ip', '/v1/{0}/host-domains'.format(ip), limit=limit, **kwargs)

    def reverse_ip_whois(self, query=None, ip=None, country=None, server=None, include_total_count=False, page=1,
                         **kwargs):
        """Pass in an IP address or a list of free text query terms."""
        if (ip and query) or not (ip or query):
            raise ValueError('Query or IP Address (but not both) must be defined')

        return self._results('reverse-ip-whois', '/v1/reverse-ip-whois', query=query, ip=ip, country=country,
                             server=server, include_total_count=include_total_count, page=page, items_path=('records',),
                             **kwargs)

    def reverse_name_server(self, query, limit=None, **kwargs):
        """Pass in a domain name or a name server."""
        return self._results('reverse-name-server', '/v1/{0}/name-server-domains'.format(query),
                             items_path=('primary_domains',), limit=limit, **kwargs)

    def reverse_whois(self, query, exclude=None, scope='current', mode='purchase', **kwargs):
        """List of one or more terms to search for in the Whois record,
           as a Python list or separated with the pipe character ( | ).
        """
        if exclude is None:
            exclude = []
        return self._results('reverse-whois', '/v1/reverse-whois', terms=delimited(query), exclude=delimited(exclude),
                             scope=scope, mode=mode, **kwargs)

    def whois(self, query, **kwargs):
        """Pass in a domain name or an IP address to perform a whois lookup."""
        return self._results('whois', '/v1/{0}/whois'.format(query), **kwargs)

    def whois_history(self, query, mode=None, sort=None, offset=None, limit=None, **kwargs):
        """Pass in a domain name."""
        return self._results('whois-history', '/v1/{0}/whois/history'.format(query), mode=mode, sort=sort,
                             offset=offset, limit=limit, items_path=('history',), **kwargs)

    def phisheye(self, query, days_back=None, **kwargs):
        """Returns domain results for the specified term for today or the specified number of days_back.
           Terms must be setup for monitoring via the web interface: https://research.domaintools.com/phisheye.

           NOTE: Properties of a domain are only provided if we have been able to obtain them.
                 Many domains will have incomplete data because that information isn't available in their Whois records,
                 or they don't have DNS results for a name server or IP address.
        """
        return self._results('phisheye', '/v1/phisheye', query=query, days_back=days_back, items_path=('domains',),
                             **kwargs)

    def phisheye_term_list(self, include_inactive=False, **kwargs):
        """Provides a list of terms that are set up for this account.
           This call is not charged against your API usage limit.

           NOTE: The terms must be configured in the PhishEye web interface: https://research.domaintools.com/phisheye.
                 There is no API call to set up the terms.
        """
        return self._results('phisheye_term_list', '/v1/phisheye/term-list', include_inactive=include_inactive,
                             items_path=('terms',), **kwargs)

    def iris(self, domain=None, ip=None, email=None, nameserver=None, registrar=None, registrant=None,
             registrant_org=None, **kwargs):
        """Performs a search for the provided search terms ANDed together,
           returning the pivot engine row data for the resulting domains.
        """
        if ((not domain and not ip and not email and not nameserver and not registrar and not registrant and not
             registrant_org and not kwargs)):
            raise ValueError('At least one search term must be specified')

        return self._results('iris', '/v1/iris', domain=domain, ip=ip, email=email, nameserver=nameserver,
                             registrar=registrar, registrant=registrant, registrant_org=registrant_org,
                             items_path=('results',), **kwargs)

    def risk(self, domain, **kwargs):
        """Returns back the risk score for a given domain"""
        return self._results('risk', '/v1/risk', items_path=('components',), domain=domain, cls=Reputation,
                             **kwargs)

    def risk_evidence(self, domain, **kwargs):
        """Returns back the detailed risk evidence associated with a given domain"""
        return self._results('risk-evidence', '/v1/risk/evidence/', items_path=('components',), domain=domain,
                             **kwargs)



    def iris_enrich_cli(self, domains=None, **kwargs):
        """Returns back enriched data related to the specified domains using our Iris Enrich service.
            This is a CLI version of the iris_enrich method to help maintain backwards compatibility.

           api.iris_enrich(['domaintools.com', 'google.com'])

           api.iris_enrich(DOMAIN_LIST)['results_count'] Returns the number of results
           api.iris_enrich(DOMAIN_LIST)['missing_domains'] Returns any domains that we were unable to
                                                           retrieve enrichment data for
           api.iris_enrich(DOMAIN_LIST)['limit_exceeded'] Returns True if you've exceeded your API usage

           for enrichment in api.iris_enrich(DOMAIN_LIST):  # Enables looping over all returned enriched domains

           for example:
               enrich_domains = ['google.com', 'amazon.com']
               assert api.iris_enrich(*enrich_domains)['missing_domains'] == []
        """
        if not domains:
            raise ValueError('One or more domains to enrich must be provided')

        if isinstance(domains, (list, tuple)):
            domains = ','.join(domains)
        data_updated_after = kwargs.get('data_updated_after', None)
        if hasattr(data_updated_after, 'strftime'):
            data_updated_after = data_updated_after.strftime('%Y-%m-%d')

        return self._results('iris-enrich', '/v1/iris-enrich/', domain=domains, data_updated_after=data_updated_after,
                             items_path=('results',), **kwargs)


    def iris_investigate_cli(self, domains=None, **kwargs):
        """
        Returns back investigation data related to the specified domains using our Iris Investigate service.
        This is a CLI version of the iris_investigate method to help maintain backwards compatibility.
        
        For example:
            investigate_domains = ['google.com', 'amazon.com']
            assert api.iris_investigate_cli(*investigate_domains)['missing_domains'] == []
        """
        if not domains:
            raise ValueError('One or more domains to investigate must be provided')

        # Handle the domains, whether passed as a list, tuple, or individual domains
        if isinstance(domains, (list, tuple)):
            domains = ','.join(domains)
        elif isinstance(domains, str):
            domains = domains
        
        # Format the additional parameters that require special formatting (e.g., dates)
        data_updated_after = kwargs.get('data_updated_after', None)
        if hasattr(data_updated_after, 'strftime'):
            data_updated_after = data_updated_after.strftime('%Y-%m-%d')
        
        # Call the _results method similarly to iris_enrich_cli, adjusting the endpoint as needed
        result = self._results('iris-investigate', '/v1/iris-investigate/', domain=domains,
                               data_updated_after=data_updated_after, **kwargs)
                               
        return result
    def iris_detect_monitors(self, include_counts=False, datetime_counts_since=None, sort=None, order="desc", offset=0,
                             limit=None, **kwargs):
        """Returns back a list of monitors in Iris Detect based on the provided filters.

        include_counts: bool: default False. includes counts for each monitor for new, watched, changed, and escalated
        domains

        datetime_counts_since: ISO 8601 datetime format: default None. Conditionally required if the include_counts
        parameter is set to True.

        sort: List[str]: default ["term"]. Sort order for monitor list. Valid values are an ordered list of the following:
         ["term", "created_date", "domain_counts_changed", "domain_counts_discovered"]

        order: str: default "desc". Sort order "asc" or "desc"

        offset: int: default 0. Offset for pagination

        limit: int: default 500. Limit for pagination. Restricted to maximum 100 if include_counts is set to True.
        """

        if include_counts:
            if not datetime_counts_since:
                raise ValueError('Need to define datetime_counts_since when include_counts is True')
            if isinstance(datetime_counts_since, datetime):
                datetime_counts_since = str(datetime_counts_since.astimezone())
            elif isinstance(datetime_counts_since, str):
                kwargs["datetime_counts_since"] = datetime_counts_since
            kwargs["include_counts"] = "true"
            kwargs["datetime_counts_since"] = datetime_counts_since
        if sort:
            kwargs["sort[]"] = sort
        return self._results('iris-detect-monitors', '/v1/iris-detect/monitors/', order=order, offset=offset,
                             limit=limit, items_path=('monitors',), response_path=(), **kwargs)

    def iris_investigate(self, domains=None, data_updated_after=None, expiration_date=None,
                         create_date=None, active=None, search_hash=None, **kwargs):
        def convert_date(date_param):
            return date_param.strftime('%Y-%m-%d') if hasattr(date_param, 'strftime') else date_param

        
        aggregated_results = {
            'limit_exceeded': False,
            'has_more_results': True,
            'message': 'Enjoy your data.',
            'results_count': 0,
            'total_count': 0,
            'results': [],
            'missing_domains': [],
            'position': None  
        }

        # Convert parameters to string format as necessary
        data_updated_after = convert_date(data_updated_after)
        expiration_date = convert_date(expiration_date)
        create_date = convert_date(create_date)
        active_str = str(active).lower() if isinstance(active, bool) else active
        if search_hash:
            kwargs['search_hash'] = search_hash

        # Join given domains if list or tuple
        if isinstance(domains, (list, tuple)):
            domains = ','.join(domains)

        # Check if batch processing is needed
        batch_size = 100
        if isinstance(domains, str) and len(domains.split(',')) > batch_size:
            domains_list = domains.split(',')
            for i in range(0, len(domains_list), batch_size):
                batch_domains = domains_list[i:i + batch_size]
                batch_result = self.iris_investigate(
                    batch_domains, data_updated_after, expiration_date, create_date, active_str, search_hash, **kwargs)
                
                # Aggregate the batch results
                aggregated_results['results_count'] += batch_result['results_count']
                aggregated_results['total_count'] += batch_result['total_count']
                aggregated_results['limit_exceeded'] |= batch_result.get('limit_exceeded', False)
                aggregated_results['results'].extend(batch_result['results'])
                aggregated_results['missing_domains'].extend(batch_result.get('missing_domains', []))
                
            aggregated_results['has_more_results'] = aggregated_results['results_count'] < aggregated_results['total_count']
        else:
            # Handle non-batched requests or single domain requests
            kwargs['active'] = active_str
            while True:
                response = self._results(
                    'iris-investigate', '/v1/iris-investigate/', domain=domains,
                    data_updated_after=data_updated_after, expiration_date=expiration_date,
                    create_date=create_date, position=aggregated_results['position'], items_path=('results',), **kwargs)

                # Aggregate the results
                aggregated_results['results_count'] += response['results_count']
                aggregated_results['total_count'] = response['total_count']
                aggregated_results['limit_exceeded'] |= response.get('limit_exceeded', False)
                aggregated_results['results'].extend(response.get('results', []))
                aggregated_results['position'] = response.get('position', None)
                aggregated_results['has_more_results'] = response.get('has_more_results', False)
                aggregated_results['missing_domains'].extend(response.get('missing_domains', []))

                if not response.get('has_more_results', False):
                    break

        return aggregated_results

    def iris_enrich(self, *domains, **kwargs):
        # Flatten the possible nested list from *domains using a generator expression
        all_domains = (d for domain in domains for d in (domain if isinstance(domain, list) else [domain]))
        all_domains = list(all_domains)  # Convert generator to list, ensuring only one iteration over input domains

        # Set batch size limit
        batch_size = 100

        # Check if batched processing is needed
        batched = kwargs.get('batched', False)
        if batched:
            del kwargs['batched']
        
        if len(all_domains) > batch_size and not batched:
            # Handle batching by splitting domains into chunks and calling iris_enrich recursively for each batch
            aggregated_results = {
                'limit_exceeded': False,
                "message": "Enjoy your data.",
                'results_count': 0,
                'results': [],
                'missing_domains': [],
                                
            }

            for i in range(0, len(all_domains), batch_size):
                batch_domains = all_domains[i:i + batch_size]
                batch_result = self.iris_enrich(*batch_domains, batched=True, **kwargs)

                aggregated_results['results_count'] += batch_result['results_count']
                aggregated_results['missing_domains'].extend(batch_result['missing_domains'])
                if batch_result['limit_exceeded']:
                    aggregated_results['limit_exceeded'] = True
                aggregated_results['results'].extend(batch_result['results'])

            return aggregated_results
        else:
            # Process a single domain or smaller batch of domains
            if not all_domains:
                raise ValueError('One or more domains to enrich must be provided')

            domains_str = ','.join(all_domains)
            data_updated_after = kwargs.get('data_updated_after', None)
            if hasattr(data_updated_after, 'strftime'):
                data_updated_after = data_updated_after.strftime('%Y-%m-%d')

            # Make the API call for single or smaller batches and return the single result set
            return self._results('iris-enrich', '/v1/iris-enrich/', domain=domains_str,
                                 data_updated_after=data_updated_after, items_path=('results',), **kwargs)
            
    def iris_detect_new_domains(self, monitor_id=None, tlds=None, risk_score_ranges=None,
                                mx_exists=None, discovered_since=None, changed_since=None, search=None, sort=None,
                                order=None, include_domain_data=False, offset=0, limit=None, preview=None, **kwargs):
        """Returns back a list of new domains in Iris Detect based on the provided filters.

        monitor_id: str: default None. Monitor ID from monitors response. Only used when requesting domains for a
        specific monitor.

        tlds: List[str]: default None. List of TLDs to filter domains by.

        risk_score_ranges: List[str]: default None. List of risk score ranges to filter domains by. Valid values are:
        ["0-0", "1-39", "40-69", "70-99", "100-100"]

        mx_exists: bool: default None. Filter domains by if they have an MX record in DNS.

        discovered_since: ISO 8601 datetime format: default None. Filter domains by when they were discovered.
        Most relevant for iris_detect_new_domains endpoint to control the timeframe for when a new domain was discovered.

        changed_since: ISO 8601 datetime format: default None. Filter domains by when they were last changed.
        Most relevant for the iris_detect_watched_domains endpoint to control the timeframe for changes to DNS or whois
        fields for watched domains.

        search: str: default None. A "contains" search for any portion of a domain name.

        sort: List[str]: default None. Sort order for domain list. Valid values are an ordered list of the following:
        ["discovered_date", "changed_date", "risk_score"]

        order: str: default None. Sort order "asc" or "desc"

        include_domain_data: bool: default False. Includes DNS and whois data in the response.

        offset: int: default 0. Offset for pagination

        limit: int: default 100. Limit for pagination. Restricted to maximum 50 if include_domain_data is set to True.

        preview: bool: default None. Preview mode used for testing. If set to True, only the first 10 results are
        returned but not limited by hourly restrictions.
        """
        if discovered_since:
            if isinstance(discovered_since, datetime):
                kwargs["discovered_since"] = str(discovered_since.astimezone())
            elif isinstance(discovered_since, str):
                kwargs["discovered_since"] = discovered_since
        if changed_since:
            if isinstance(changed_since, datetime):
                kwargs["changed_since"] = str(changed_since.astimezone())
            elif isinstance(changed_since, str):
                kwargs["changed_since"] = changed_since
        if tlds:
            kwargs["tlds[]"] = tlds
        if risk_score_ranges:
            kwargs["risk_score_ranges[]"] = risk_score_ranges
        if sort:
            kwargs["sort[]"] = sort
        if order is not None:
            kwargs["order"] = order
        if mx_exists is not None:
            kwargs["mx_exists"] = mx_exists
        return self._results('iris-detect-new-domains', '/v1/iris-detect/domains/new/', monitor_id=monitor_id,
                             search=search, include_domain_data=include_domain_data, preview=preview, offset=offset,
                             limit=limit, items_path=('watchlist_domains',), response_path=(), **kwargs)

    def iris_detect_watched_domains(self, monitor_id=None, escalation_types=None, tlds=None, risk_score_ranges=None,
                                mx_exists=None, discovered_since=None, changed_since=None, escalated_since=None, search=None, sort=None,
                                order=None, include_domain_data=False, offset=0, limit=None, preview=None, **kwargs):
        """Returns back a list of watched domains in Iris Detect based on the provided filters.

        monitor_id: str: default None. Monitor ID from monitors response. Only used when requesting domains for a
        specific monitor.

        escalation_types: List[str]: default None. List of escalation types to filter domains by. Valid values are:
        ["blocked", "google_safe"]

        tlds: List[str]: default None. List of TLDs to filter domains by.

        risk_score_ranges: List[str]: default None. List of risk score ranges to filter domains by. Valid values are:
        ["0-0", "1-39", "40-69", "70-99", "100-100"]

        mx_exists: bool: default None. Filter domains by if they have an MX record in DNS.

        discovered_since: ISO 8601 datetime format: default None. Filter domains by when they were discovered.
        Most relevant for iris_detect_new_domains endpoint to control the timeframe for when a new domain was discovered.

        changed_since: ISO 8601 datetime format: default None. Filter domains by when they were last changed.
        Most relevant for the iris_detect_watched_domains endpoint to control the timeframe for changes to DNS or whois
        fields for watched domains.

        escalated_since: ISO 8601 datetime format: default None. Filter domains by when they were last escalated.
        Most relevant for the iris_detect_watched_domains endpoint to control the timeframe for when a domain was most
        recently escalated.

        search: str: default None. A "contains" search for any portion of a domain name.

        sort: List[str]: default None. Sort order for domain list. Valid values are an ordered list of the following:
        ["discovered_date", "changed_date", "risk_score"]

        order: str: default None. Sort order "asc" or "desc"

        include_domain_data: bool: default False. Includes DNS and whois data in the response.

        offset: int: default 0. Offset for pagination

        limit: int: default 100. Limit for pagination. Restricted to maximum 50 if include_domain_data is set to True.

        preview: bool: default None. Preview mode used for testing. If set to True, only the first 10 results are
        returned but not limited by hourly restrictions.
        """
        if discovered_since:
            if isinstance(discovered_since, datetime):
                kwargs["discovered_since"] = str(discovered_since.astimezone())
            elif isinstance(discovered_since, str):
                kwargs["discovered_since"] = discovered_since
        if changed_since:
            if isinstance(changed_since, datetime):
                kwargs["changed_since"] = str(changed_since.astimezone())
            elif isinstance(changed_since, str):
                kwargs["changed_since"] = changed_since
        if escalated_since:
            if isinstance(escalated_since, datetime):
                kwargs["escalated_since"] = str(escalated_since.astimezone())
            elif isinstance(escalated_since, str):
                kwargs["escalated_since"] = escalated_since
        if escalation_types:
            kwargs["escalation_types[]"] = escalation_types
        if tlds:
            kwargs["tlds[]"] = tlds
        if risk_score_ranges:
            kwargs["risk_score_ranges[]"] = risk_score_ranges
        if sort:
            kwargs["sort[]"] = sort
        if order is not None:
            kwargs["order"] = order
        if mx_exists is not None:
            kwargs["mx_exists"] = mx_exists
        return self._results('iris-detect-watched-domains', '/v1/iris-detect/domains/watched/', monitor_id=monitor_id,
                             search=search, include_domain_data=include_domain_data, preview=preview, offset=offset,
                             limit=limit, items_path=('watchlist_domains',), response_path=(), **kwargs)

    def iris_detect_manage_watchlist_domains(self, watchlist_domain_ids, state, **kwargs):
        """Changes the watch state of a list of domains by their Iris Detect domain ID.

        watchlist_domain_ids: List[str]: required. List of Iris Detect domain IDs to manage.

        state: str: required. Valid values are: ["watched", "ignored"]
        """
        if isinstance(watchlist_domain_ids, str):
            watchlist_domain_ids = watchlist_domain_ids.split(',')
        return self._results('iris-detect-manage-watchlist-domains', '/v1/iris-detect/domains/', state=state,
                             watchlist_domain_ids=watchlist_domain_ids, items_path=('watchlist_domains',),
                             response_path=(), **kwargs)

    def iris_detect_escalate_domains(self, watchlist_domain_ids, escalation_type, **kwargs):
        """Changes the escalation type of a list of domains by their Iris Detect domain ID.

        watchlist_domain_ids: List[str]: required. List of Iris Detect domain IDs to escalate.

        escalation_type: str: required. Valid values are: ["blocked", "google_safe"]
        """
        kwargs["watchlist_domain_ids[]"] = watchlist_domain_ids
        return self._results('iris-detect-escalate-domains', '/v1/iris-detect/escalations/',
                             escalation_type=escalation_type, items_path=('escalations',),
                             response_path=(), **kwargs)

    def iris_detect_ignored_domains(self, monitor_id=None, escalation_types=None, tlds=None, risk_score_ranges=None,
                                mx_exists=None, discovered_since=None, changed_since=None, escalated_since=None, search=None, sort=None,
                                order=None, include_domain_data=False, offset=0, limit=None, preview=None, **kwargs):
        """Returns back a list of ignored domains in Iris Detect based on the provided filters.

        monitor_id: str: default None. Monitor ID from monitors response. Only used when requesting domains for a
        specific monitor.

        escalation_types: List[str]: default None. List of escalation types to filter domains by. Valid values are:
        ["blocked", "google_safe"]

        tlds: List[str]: default None. List of TLDs to filter domains by.

        risk_score_ranges: List[str]: default None. List of risk score ranges to filter domains by. Valid values are:
        ["0-0", "1-39", "40-69", "70-99", "100-100"]

        mx_exists: bool: default None. Filter domains by if they have an MX record in DNS.

        discovered_since: ISO 8601 datetime format: default None. Filter domains by when they were discovered.
        Most relevant for iris_detect_new_domains endpoint to control the timeframe for when a new domain was discovered.

        changed_since: ISO 8601 datetime format: default None. Filter domains by when they were last changed.
        Most relevant for the iris_detect_watched_domains endpoint to control the timeframe for changes to DNS or whois
        fields for watched domains.

        escalated_since: ISO 8601 datetime format: default None. Filter domains by when they were last escalated.
        Most relevant for the iris_detect_watched_domains endpoint to control the timeframe for when a domain was most
        recently escalated.

        search: str: default None. A "contains" search for any portion of a domain name.

        sort: List[str]: default None. Sort order for domain list. Valid values are an ordered list of the following:
        ["discovered_date", "changed_date", "risk_score"]

        order: str: default None. Sort order "asc" or "desc"

        include_domain_data: bool: default False. Includes DNS and whois data in the response.

        offset: int: default 0. Offset for pagination

        limit: int: default 100. Limit for pagination. Restricted to maximum 50 if include_domain_data is set to True.

        preview: bool: default None. Preview mode used for testing. If set to True, only the first 10 results are
        returned but not limited by hourly restrictions.
        """
        if discovered_since:
            if isinstance(discovered_since, datetime):
                kwargs["discovered_since"] = str(discovered_since.astimezone())
            elif isinstance(discovered_since, str):
                kwargs["discovered_since"] = discovered_since
        if changed_since:
            if isinstance(changed_since, datetime):
                kwargs["changed_since"] = str(changed_since.astimezone())
            elif isinstance(changed_since, str):
                kwargs["changed_since"] = changed_since
        if escalated_since:
            if isinstance(escalated_since, datetime):
                kwargs["escalated_since"] = str(escalated_since.astimezone())
            elif isinstance(escalated_since, str):
                kwargs["escalated_since"] = escalated_since
        if escalation_types:
            kwargs["escalation_types[]"] = escalation_types
        if tlds:
            kwargs["tlds[]"] = tlds
        if risk_score_ranges:
            kwargs["risk_score_ranges[]"] = risk_score_ranges
        if sort:
            kwargs["sort[]"] = sort
        if order is not None:
            kwargs["order"] = order
        if mx_exists is not None:
            kwargs["mx_exists"] = mx_exists
        return self._results('iris-detect-ignored-domains', '/v1/iris-detect/domains/ignored/', monitor_id=monitor_id,
                             search=search, include_domain_data=include_domain_data, preview=preview, offset=offset,
                             limit=limit, items_path=('watchlist_domains',), response_path=(), **kwargs)
