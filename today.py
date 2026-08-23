import datetime
from dateutil import relativedelta
import requests
import os
import time
import hashlib

try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree
    etree.register_namespace('', 'http://www.w3.org/2000/svg')

# Fine-grained personal access token with All Repositories access:
# Account permissions: read:Followers, read:Starring, read:Watching
# Repository permissions: read:Commit statuses, read:Contents, read:Issues, read:Metadata, read:Pull Requests
ACCESS_TOKEN = os.environ.get('ACCESS_TOKEN', '')
USER_NAME = os.environ.get('USER_NAME', 'Raph1710')

HEADERS = {'authorization': f'token {ACCESS_TOKEN}'} if ACCESS_TOKEN else {}
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'loc_query': 0}

# EDIT ME: the date you started coding professionally / seriously.
# This powers the "Coding since" line on the card.
CODING_START_DATE = datetime.datetime(2022, 1, 1)


def coding_duration(start_date):
    """
    Returns the length of time since CODING_START_DATE
    e.g. '4 years, 7 months, 5 days'
    """
    diff = relativedelta.relativedelta(datetime.datetime.today(), start_date)
    return '{} {}, {} {}, {} {}'.format(
        diff.years, 'year' + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days, 'day' + format_plural(diff.days))


def format_plural(unit):
    """
    Returns a properly formatted number
    e.g. 'day' + format_plural(diff.days) == '5 days', '1 day'
    """
    return 's' if unit != 1 else ''


def simple_request(func_name, query, variables):
    """
    Returns a request, or raises an Exception if the response does not succeed.
    """
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables}, headers=HEADERS)
    if request.status_code == 200:
        return request
    raise Exception(func_name, ' has failed with a', request.status_code, request.text, QUERY_COUNT)


def graph_repos_stars(count_type, owner_affiliation, cursor=None, edges=None):
    """
    Uses GitHub's GraphQL v4 API to return total repository or star count.
    Supports pagination across multiple pages of repositories.
    """
    if edges is None:
        edges = []
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(graph_repos_stars.__name__, query, variables)
    res_data = request.json().get('data', {}).get('user', {}).get('repositories', {})
    if count_type == 'repos':
        return res_data.get('totalCount', 0)
    elif count_type == 'stars':
        edges += res_data.get('edges', [])
        page_info = res_data.get('pageInfo', {})
        if page_info.get('hasNextPage'):
            return graph_repos_stars(count_type, owner_affiliation, page_info.get('endCursor'), edges)
        return stars_counter(edges)


def recursive_loc(owner, repo_name, data, cache_comment, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    """
    Uses GitHub's GraphQL v4 API and cursor pagination to fetch 100 commits from a repository at a time
    """
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables}, headers=HEADERS)
    if request.status_code == 200:
        repo_data = request.json().get('data', {}).get('repository', {})
        branch_ref = repo_data.get('defaultBranchRef') if repo_data else None
        if branch_ref and branch_ref.get('target') and branch_ref['target'].get('history'):
            return loc_counter_one_repo(owner, repo_name, data, cache_comment, branch_ref['target']['history'], addition_total, deletion_total, my_commits)
        else:
            return 0, 0, 0
    force_close_file(data, cache_comment)
    if request.status_code == 403:
        raise Exception('Too many requests in a short amount of time!\nYou\'ve hit the non-documented anti-abuse limit!')
    raise Exception('recursive_loc() has failed with a', request.status_code, request.text, QUERY_COUNT)


def loc_counter_one_repo(owner, repo_name, data, cache_comment, history, addition_total, deletion_total, my_commits):
    """
    Recursively calls recursive_loc (GraphQL can only return 100 commits at a time)
    Only adds the LOC value of commits authored by me.
    Includes null-checks for commit author and author user to prevent TypeError crashes.
    """
    edges = history.get('edges', [])
    for node in edges:
        commit_node = node.get('node', {})
        author = commit_node.get('author')
        user_obj = author.get('user') if author else None
        if user_obj and user_obj == OWNER_ID:
            my_commits += 1
            addition_total += commit_node.get('additions', 0)
            deletion_total += commit_node.get('deletions', 0)

    page_info = history.get('pageInfo', {})
    if not edges or not page_info.get('hasNextPage'):
        return addition_total, deletion_total, my_commits
    else:
        return recursive_loc(owner, repo_name, data, cache_comment, addition_total, deletion_total, my_commits, page_info.get('endCursor'))


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=None):
    """
    Uses GitHub's GraphQL v4 API to query all repositories I have access to (w.r.t. owner_affiliation)
    Queries 60 repos at a time to avoid timeouts. Returns total lines of code across all repositories.
    """
    if edges is None:
        edges = []
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            defaultBranchRef {
                                target {
                                    ... on Commit {
                                        history {
                                            totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(loc_query.__name__, query, variables)
    res_data = request.json().get('data', {}).get('user', {}).get('repositories', {})
    edges += res_data.get('edges', [])
    page_info = res_data.get('pageInfo', {})
    if page_info.get('hasNextPage'):
        return loc_query(owner_affiliation, comment_size, force_cache, page_info.get('endCursor'), edges)
    else:
        return cache_builder(edges, comment_size, force_cache)


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    """
    Checks each repository to see if it has been updated since the last time it was cached.
    If it has, runs recursive_loc on that repository to update the LOC count.
    """
    cached = True
    os.makedirs('cache', exist_ok=True)
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError:
        data = []
        if comment_size > 0:
            for _ in range(comment_size):
                data.append('This line is a comment block. Write whatever you want here.\n')
        with open(filename, 'w') as f:
            f.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        cached = False
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size]
    data = data[comment_size:]
    for index in range(len(edges)):
        repo_node = edges[index].get('node', {})
        name_with_owner = repo_node.get('nameWithOwner', '')
        repo_hash = hashlib.sha256(name_with_owner.encode('utf-8')).hexdigest()

        branch_ref = repo_node.get('defaultBranchRef')
        current_commit_count = 0
        if branch_ref and branch_ref.get('target') and branch_ref['target'].get('history'):
            current_commit_count = branch_ref['target']['history'].get('totalCount', 0)

        line_parts = data[index].split() if index < len(data) else []
        cached_hash = line_parts[0] if len(line_parts) > 0 else ''
        cached_commits = int(line_parts[1]) if len(line_parts) > 1 else -1

        if cached_hash == repo_hash:
            if cached_commits != current_commit_count:
                if name_with_owner and '/' in name_with_owner and current_commit_count > 0:
                    owner, repo_name = name_with_owner.split('/')
                    loc = recursive_loc(owner, repo_name, data, cache_comment)
                    if loc and len(loc) == 3:
                        data[index] = f"{repo_hash} {current_commit_count} {loc[2]} {loc[0]} {loc[1]}\n"
                    else:
                        data[index] = f"{repo_hash} {current_commit_count} 0 0 0\n"
                else:
                    data[index] = f"{repo_hash} 0 0 0 0\n"
        else:
            data[index] = f"{repo_hash} 0 0 0 0\n"

    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)

    for line in data:
        loc = line.split()
        if len(loc) >= 5:
            loc_add += int(loc[3])
            loc_del += int(loc[4])
    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    """
    Wipes the cache file. Called when the number of repositories changes or the file is first created.
    """
    data = []
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            if comment_size > 0:
                data = f.readlines()[:comment_size]
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            name = node.get('node', {}).get('nameWithOwner', '')
            f.write(hashlib.sha256(name.encode('utf-8')).hexdigest() + ' 0 0 0 0\n')


def force_close_file(data, cache_comment):
    """
    Forces the cache file to close, preserving whatever data was written so far,
    in case the program crashes mid-run.
    """
    os.makedirs('cache', exist_ok=True)
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    print('There was an error while writing to the cache file. The file,', filename, 'has had the partial data saved and closed.')


def stars_counter(data):
    """
    Counts total stars across repositories owned by me
    """
    total_stars = 0
    for node in data:
        stargazers = node.get('node', {}).get('stargazers', {})
        total_stars += stargazers.get('totalCount', 0)
    return total_stars


def svg_overwrite(filename, coding_time, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    """
    Parses the SVG template and updates the dynamic elements with fresh stats.
    """
    tree = etree.parse(filename)
    root = tree.getroot()
    justify_format(root, 'age_data', coding_time, 22)
    justify_format(root, 'commit_data', commit_data, 22)
    justify_format(root, 'star_data', star_data, 14)
    justify_format(root, 'repo_data', repo_data, 6)
    justify_format(root, 'contrib_data', contrib_data)
    justify_format(root, 'follower_data', follower_data, 10)
    justify_format(root, 'loc_data', loc_data[2], 9)
    justify_format(root, 'loc_add', loc_data[0])
    justify_format(root, 'loc_del', loc_data[1], 7)
    tree.write(filename, encoding='utf-8', xml_declaration=True)


def justify_format(root, element_id, new_text, length=0):
    """
    Updates the text of an element and pads the preceding dot-leader element to keep things justified.
    """
    if isinstance(new_text, int):
        new_text = f"{'{:,}'.format(new_text)}"
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)
    just_len = max(0, length - len(new_text))
    if just_len <= 2:
        dot_map = {0: '', 1: ' ', 2: '. '}
        dot_string = dot_map[just_len]
    else:
        dot_string = ' ' + ('.' * just_len) + ' '
    find_and_replace(root, f"{element_id}_dots", dot_string)


def find_and_replace(root, element_id, new_text):
    """
    Finds the element in the SVG file and replaces its text with a new value
    """
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


def commit_counter(comment_size):
    """
    Counts up total commits, using the cache file created by cache_builder.
    """
    total_commits = 0
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    if not os.path.exists(filename):
        return 0
    with open(filename, 'r') as f:
        data = f.readlines()
    data = data[comment_size:]
    for line in data:
        parts = line.split()
        if len(parts) >= 3:
            total_commits += int(parts[2])
    return total_commits


def user_getter(username):
    """
    Returns the account ID and creation time of the user
    """
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    variables = {'login': username}
    request = simple_request(user_getter.__name__, query, variables)
    user_res = request.json().get('data', {}).get('user', {})
    return {'id': user_res.get('id')}, user_res.get('createdAt')


def follower_getter(username):
    """
    Returns the number of followers of the user
    """
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    request = simple_request(follower_getter.__name__, query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])


def query_count(funct_id):
    """
    Counts how many times the GitHub GraphQL API is called
    """
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    """
    Calculates the time it takes for a function to run
    """
    start = time.perf_counter()
    funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


def formatter(query_type, difference):
    """
    Prints a formatted time differential
    """
    print('{:<23}'.format('   ' + query_type + ':'), sep='', end='')
    print('{:>12}'.format('%.4f' % difference + ' s ')) if difference > 1 else print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))


if __name__ == '__main__':
    """
    Raphael D'Almeida — dynamic GitHub profile card
    Adapted from Andrew Grant's (Andrew6rant) profile generator: https://github.com/Andrew6rant/Andrew6rant
    """
    print('Calculation times:')
    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date = user_data
    formatter('account data', user_time)

    coding_time, coding_time_calc = perf_counter(coding_duration, CODING_START_DATE)
    formatter('coding duration', coding_time_calc)

    total_loc, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 7)
    formatter('LOC (cached)' if total_loc[-1] else 'LOC (no cache)', loc_time)

    commit_data, commit_time = perf_counter(commit_counter, 7)
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data, contrib_time = perf_counter(graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)

    for index in range(len(total_loc) - 1):
        total_loc[index] = '{:,}'.format(total_loc[index])

    svg_overwrite('dark_mode.svg', coding_time, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])
    svg_overwrite('light_mode.svg', coding_time, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])

    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items():
        print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))
