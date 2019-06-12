import sys
import requests
from bs4 import BeautifulSoup
import urllib
import pandas as pd
import json
from time import sleep, perf_counter as pf
import regex as re
import itertools as it
import patterns as pt

#requests.packages.urllib3.util.ssl_.DEFAULT_CIPHERS += ':DES-CBC3-SHA'
#timetable_url = 'https://web30.uottawa.ca/v3/SITS/timetable/Search.aspx'
# Course Info Parameters
course_url = 'https://catalogue.uottawa.ca/en/courses/'

# Timetable Parameters
with open("template_query.json", "r") as f:
    old_form = json.load(f)
term_to_num = {"fall": "9", "summer": "5", "winter":"1"}
orig_link = 'https://uocampus.public.uottawa.ca/psc/csprpr9pub/EMPLOYEE/HRMS/c/UO_SR_AA_MODS.UO_PUB_CLSSRCH.GBL'
default_headers={'Content-Type':"application/x-www-form-urlencoded"}

#############################################################################
# COURSE INFO SCRAPING
#############################################################################

def _extract_codes(string, return_all = True):
    '''
    Returns course codes found in string; 
    if multiple codes are found and return_all is False, then returns an invalid code
    Used in get_subjects.ipynb ''' 
    codes = list({x.group(0) for x in re.finditer(pt.code_re, string)})
    if return_all or len(codes) == 1:
        return codes
    return 'XXX 0000'

def _extract_credits(string):
    '''
    Searches string for a number of credits/units
    (Assuming the string is the title of a course)
    Used in get_subjects.ipynb
    '''
    credits = list({int(x.group(0).split(' ')[0].strip('(')) for x in re.finditer(pt.credit_re, string)})
    if len(credits) == 1:
        return credits
    return [0]

class Prereq:
    '''
    Object used to hold information about prereqs and other information that is provided in the courseblockextra section of the uOttawa catalogue
    '''
    rep_codes = {
        "credit_count" : "YYY0000",
        "ForU" : "YYY0001",
        "for_special_program" : ") or ("
    }

    def parse_codes(self, parsable):
        '''
        Turns a string of parsable codes into a list of prerequiste groups that are each sufficient to get into the course
        A string of parsable codes is defined as any string of course codes seperated by any of [/ or ou , and et] and parentheses for priotirty.
        '''

        parsable = self.match_to_string(parsable)

        #Replace all parenthesised groups with a unique fake course code. This makes it possible to look at
        #each group as a single course until we sub them back in later and apply the rules we need for them.
        #Essentially this is priority of operations but it's easier to do them in reverse here
        replacement_codes = ("XXX%04d" % i for i in it.count())
        codes = []
        for code in replacement_codes:
            pos = -1
            for i, c in enumerate(parsable):
                if c == '(':
                    pos = i
                elif c == ')':
                    codes.append((code, parsable[pos+1:i]))
                    parsable = parsable[:pos] + code + parsable[i+1:]
                    break   #This increments to the next replacement code and starts from the beginning of the string
            else:   #When we reach the end of the string we're done
                break

        #Add final group to the codes array to handle top level or groups
        code = next(replacement_codes)
        codes.append((code, parsable))
        prereq_groups = [[code]]

        #Handle mixed "and" and "or" groups into individual codes
        new_codes = []
        for code, replacement in codes:
            replacement = replacement.split(", ")
            if len(replacement) > 1:
                for i, item in enumerate(replacement):
                    if " or " in item:
                        new_code = next(replacement_codes)
                        new_codes.append((new_code, item))
                        replacement[i] = new_code
            new_codes.append((code, ", ".join(replacement)))
        codes = new_codes

        #Sub fake codes back in for a list of possible prereq groups
        for code, replacement in reversed(codes):
            while True: #This is to overcome the problem with modifying a list as you iterate over it, kind of a hack but it works
                for group in prereq_groups:
                    if code in group:
                        if ", " in replacement:
                            group.remove(code)
                            group += replacement.split(", ")
                        elif " or " in replacement:
                            prereq_groups.remove(group)
                            group.remove(code)
                            for c in replacement.split(" or "):
                                prereq_groups.append(group + [c])
                            break
                        else:
                            group.remove(code)
                            group += [replacement]
                else:
                    break

        return prereq_groups



    def match_to_string(self, match_obj):
        '''
        Converts a match object to a string that can be parsed
        by the parse_codes function and populates some substitute
        course code values if present.
        '''

        match_str = match_obj.group()
        match_str = "(" + match_str + ")"

        # The try is for when the pattern didn't include those groups,
        # the if is if they were included but not part of the match
        for key, value in self.rep_codes.items():
            try:
                if match_obj.group(key) != None:
                    self.subs[key] = match_obj.group(key)
                    match_str = match_str.replace(match_obj.group(key), value)
            except IndexError:
                pass

        match_str = match_str.replace(" ou ", " or ").replace("/", " or ").replace(" and ", ", ").replace(" et ", ", ")

        return match_str

    def __init__(self, prereq_str):

        self.prereqs = []
        self.subs = {
            "credit_count" : "",
            "ForU" : "",
            "for_special_program" : ""
        }

        sentences = filter(None, prereq_str.replace(' / ', '. ').split('. '))
        for sentence in sentences:
            for key, pattern in pt.prereq.items():
                match = pattern.search(sentence)
                if match is not None:
                    if key == "prerequisites":
                        self.prereqs = self.parse_codes(match)
                    break

def scrape_subjects(url=course_url):
    '''
    Scrapes the list of subjects with links to their respective course catalogues from the uOttawa website
    () -> pandas DataFrame with columns: Subject, Code, Link
    Used in get_subjects.ipynb
    '''
    page = requests.get(url).text

    soup = BeautifulSoup(page, 'html.parser')
    content = soup.find('div', attrs = {'class':'az_sitemap'})
    subj_tags = content.find_all('a', attrs = {'href':pt.href_re})

    #subj_table = []
    #for tag in subj_tags:
    #    subj_table.append([tag.string, tag['href'].strip('/').rsplit('/')[-1]])
    subj_table = [[tag.string, tag['href'].strip('/').rsplit('/')[-1]]
                  for tag in subj_tags]

    subjects = pd.DataFrame(subj_table, columns=['Subject', 'Code'])
    subjects['Code'] = subjects['Code'].str.strip().str.strip('/')
    subjects['Link'] = url + subjects['Code'] + '/'
    subjects.Subject = subjects.Subject.str.replace(pt.subj_re.pattern, '').str.strip()
    return subjects

#@TODO break up into subfunctions
def get_courses(link):
    '''
    Scrapes the page given by link for courses and their descriptions, components, prerequisites, etc.
    Used in get_subjects.ipynb
    '''
    courses = []
    raw_courses = BeautifulSoup(requests.get(link).text, 'html.parser')
    raw_courses = raw_courses.find_all('div', attrs = {'class':'courseblock'})
    for course in raw_courses:
        try:
            title = course.find('p', attrs={'class':'courseblocktitle'}).text.replace('\xa0', ' ').strip()
        except AttributeError as e:
            print(course, file=sys.stderr)
            raise e
        else:
            code = _extract_codes(title, False)[0]
            credits = _extract_credits(title)[0]
            title = re.sub(pt.code_re, '', title)
            title = re.sub(pt.credit_re, '', title).strip()
        try:
            desc = course.find('p', attrs={'class':'courseblockdesc'})
            desc = desc.text.replace('\xa0', ' ').strip()
        except AttributeError as e:
            if desc is None:
                desc = ''
            else:
                print(course)
                raise e
        #parsing the course component and prerequisite info from the courseblockextra and distinguishing them
        blocks = []
        for block in course.find_all('p', attrs={'class':'courseblockextra'}):
            try:
                blocks.append(block.text.replace('\xa0', ' ').strip().strip('.'))
            except AttributeError as e:
                print(course)
                print(block)
                raise e
        if len(blocks) == 0:
            comp = ''
            pre = ''
        elif len(blocks) == 1:
            if ("Volet" in blocks[0]) or ("Course Component" in blocks[0]):
                comp = blocks[0]
                pre = ''
            elif ("Préalable" in blocks[0]) or ("Prerequisite" in blocks[0]):
                comp = ''
                pre = blocks[0]
            else:
                comp = ''
                pre = ''
        elif len(blocks) == 2:
            cond_comp0 = ("Volet" in blocks[0]) or ("Course Component" in blocks[0])
            cond_pre1 = ("Préalable" in blocks[1]) or ("Prerequisite" in blocks[1])
            cond_comp1 = ("Volet" in blocks[1]) or ("Course Component" in blocks[1])
            cond_pre0 = ("Préalable" in blocks[0]) or ("Prerequisite" in blocks[0])
            if cond_comp0 and not cond_pre0:
                comp = blocks[0]
            elif cond_comp1 and not cond_pre1:
                comp = blocks[1]
            else:
                comp = ''
            if cond_pre0 and not cond_comp0:
                pre = blocks[0]
            elif cond_pre1 and not cond_comp1:
                pre = blocks[1]
            else:
                pre = ''
        else:
            comp = ''
            pre = ''
        #adding component info to the end of the description
        desc = desc + '\n' + comp
        desc = desc.strip()
        #getting the components from after the colon in the sentence
        comp = comp.split(':', 1)[-1].strip()
        comp = [x.strip() for x in comp.split('/')[-1].split(',')]
        #getting the prerequisites from after the colon in the sentence
        dep = Prereq(pre).prereqs
        pre = pre.split(':', 1)[-1].strip()
        #TODO: ideally we would like to save the whole Prereq object to the dataframe, but since it does not output to json, using this in the meantime
        courses.append([code, title, credits, desc, comp, pre, dep])
    return pd.DataFrame(courses, columns = ['code', 'title', 'credits', 'desc', 'components', 'prerequisites', 'dependencies'])

#############################################################################
# TIMETABLE SCRAPING
#############################################################################

# Querying Timetable
def get_hidden_inputs(text):
    return BeautifulSoup(text, 'html.parser').find_all("input", type="hidden")

def update_form(old_form, new_form):
        new_form = {x["id"]:x["value"] for x in new_form}
        old_form.update({x:y for x, y in new_form.items() if y.strip() != ''})
        old_form['ICAction'] = 'CLASS_SRCH_WRK2_SSR_PB_CLASS_SRCH'

def _separate_requests_query():
    r = requests.get(orig_link)
    new_form = get_hidden_inputs(r.text)
    update_form(old_form, new_form)
    r = requests.post(orig_link, data=old_form, headers=default_headers,
                      cookies=r.cookies)
    return r

def format_query(year, term, subject, number):
    query = old_form
    term = term.lower()
    query["CLASS_SRCH_WRK2_STRM$35$"] = "2" + str(year)[-2:] + term_to_num[term]
    query["SSR_CLSRCH_WRK_SUBJECT$0"] = subject.lower()
    query["SSR_CLSRCH_WRK_CATALOG_NBR$0"] = number
    return query

def run_query(query):
    with requests.Session() as s:
        new_form = BeautifulSoup(s.get(orig_link).text, 
                                 "html.parser").find_all("input", type="hidden")
        new_form = {x["id"]:x["value"] for x in new_form}
        query.update({x:y for x, y in new_form.items() if y.strip() != ''})
        query['ICAction'] = 'CLASS_SRCH_WRK2_SSR_PB_CLASS_SRCH'
        r = s.post(orig_link, data=query, 
                   headers={'Content-Type':"application/x-www-form-urlencoded"})
    return r.text

# Extracting Timetable
def group_by_eq(seq, equalizer):
    equiv_classes = {}
    for elt in seq:
        eq = equalizer(elt)
        if eq not in equiv_classes:
            equiv_classes[eq] = []
        equiv_classes[eq].append(elt)
    return equiv_classes

def search_tag(tag, tag_name, attribute, string, matcher=(lambda x,y:
                                                    re.search(x, y) is not None)):
    try:
        if re.compile(tag_name, re.I).match(tag.name):
            return (tag.has_attr(attribute)
                    and matcher(string, tag[attribute]))
    except:
        return False

tag_is_course = lambda x: search_tag(x, "div", 
                            "id", "win0divSSR_CLSRSLT_WRK_GROUPBOX2$", 
                            lambda x,y: y.startswith(x))
course_tag_is_title = lambda x: search_tag(x, "div", "id", 
                            "win0divSSR_CLSRSLT_WRK_GROUPBOX2GP",
                            lambda x,y: y.startswith(x))
course_tag_is_section = lambda x: search_tag(x, "tr", "id", "trSSR_CLSRCH_MTG")
section_tag_is_classname = lambda x: search_tag(x, "a", "id", "MTG_CLASSNAME")

#@TODO Break into smaller subroutines
def extract_timetables(string, year, term):
    soup = BeautifulSoup(string, "lxml")

    out = {"courses":[]}
    courses = soup(tag_is_course)
    
    for course in courses:

        title = course(course_tag_is_title)[0].text
        subject_code, course_number = pt.code_re.search(
                title).group().split()
        title = pt.code_re.sub("", title).strip().strip("-").strip()
        sections = course(course_tag_is_section)
        course_out = {"subject_code": subject_code,
                      "course_number":course_number,
                      "course_name": title,
                     "sections": []}
        course_out_sections = []
        
        for section in sections:
        
            section_name = section(section_tag_is_classname)[0].contents
            section_id, section_type = section_name[0], section_name[-1]
            section_out = {"id": section_id.strip(),
                           "type": section_id.rsplit("-")[-1].strip(),
                           "session_type": section_type.strip()}
            
            rooms = [x.strip() 
                    for x in section(lambda x: 
                                        search_tag(x, "span", 
                                                   "id", "MTG_ROOM"))[0].contents 
                    if isinstance(x, str)]
            instrs = [x.strip() 
                    for x in section(lambda x: 
                                        search_tag(x, "span", 
                                                   "id", "MTG_INSTR"))[0].contents 
                    if isinstance(x, str)]
            topic = [x.strip() 
                    for x in section(lambda x: 
                                        search_tag(x, "span", 
                                                   "id", "MTG_TOPIC"))[0].contents 
                    if isinstance(x, str)]
            dttms = [x.strip() 
                    for x in section(lambda x: 
                                        search_tag(x, "span", 
                                                   "id", "MTG_DAYTIME"))[0].contents 
                    if isinstance(x, str)]

            n = min(map(len, (rooms, instrs, topic, dttms)))
            if max(map(len, (rooms, instrs, topic, dttms))) > n:
                print("warning: bad details length in course %s %s section %s"
                      % (subject_code, course_number, section_name))
            components = [{"room": rooms[i],
                           "instructor": instrs[i],
                           "day": dttms[i].strip().split(" ",
                                     1)[0].strip().upper(),
                           "start": dttms[i].strip().split(" ",
                                     1)[-1].strip().split("-")[0].strip(),
                           "end": dttms[i].strip().split(" ",
                                     1)[-1].strip().split("-")[-1].strip(),
                           "topic": topic[i],
                           **section_out}
                                         for i in range(n)]
            course_out_sections += components
        course_out_sections = group_by_eq(course_out_sections,
                                          lambda x: x["id"][0] 
                                            if len(x["id"]) > 0
                                            else "")
        for id_, component in course_out_sections.items():
            course_out["sections"].append({"year":year,
                                           "semester": term,
                                           "id": id_,
                                           "components": component})
        out["courses"].append(course_out)
    return out

